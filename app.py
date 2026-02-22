import os
import re
import time
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple, Optional

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -----------------------------
# Constants / Config
# -----------------------------
SCHEMA_VERSION = "2.0"
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
DEMO_CONTRACT_VERSION = "2026.01"

DEMO_MODE = os.environ.get("DEMO_MODE", "1").strip().lower() in ("1", "true", "yes", "on")


# -----------------------------
# CORS (simple + safe)
# -----------------------------
@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp


# -----------------------------
# Error helper (always JSON)
# -----------------------------
def json_error(code: str, message: str, status: int = 400, hint: str = None, extra: dict = None):
    payload = {"error_code": code, "message": message}
    if hint:
        payload["hint"] = hint
    if extra and isinstance(extra, dict):
        payload.update(extra)
    return jsonify(payload), status


def policy_hash(policy_mode: str) -> str:
    base = f"{POLICY_VERSION}:{(policy_mode or DEFAULT_POLICY_MODE).strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def extract_urls(raw: str) -> List[str]:
    if not raw:
        return []
    urls = re.findall(r"https?://[^\s]+", raw.strip())
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# -----------------------------
# Domain / intent detection
# -----------------------------
def _volatility_category(text_lc: str) -> str:
    t = (text_lc or "").lower()

    # Security / compliance standards
    if any(k in t for k in ["iso 27001", "soc 2", "nist", "hipaa", "gdpr", "pci", "cis controls", "encryption", "isms"]):
        return "SECURITY"

    if any(k in t for k in ["diagnosis", "treatment", "medication", "dose", "dosage", "clinical", "guideline", "contraindication"]):
        return "MEDICAL"

    if any(k in t for k in ["court", "statute", "legal", "regulation", "lawsuit", "precedent", "held that", "ruled that"]):
        return "LEGAL"

    if any(k in t for k in ["earnings", "revenue", "stock", "market", "ipo", "insider", "share price", "returns"]):
        return "FINANCIAL"

    if any(k in t for k in ["wire", "transfer", "send $", "send ", "treasury", "vendor id", "invoice", "approve", "approved by", "pay "]):
        return "OPERATIONS"

    if any(k in t for k in ["resigned", "announced", "today", "yesterday", "breaking", "just acquired", "acquired"]):
        return "NEWS"

    return "GENERAL"


def _is_volatile(text_lc: str) -> bool:
    t = (text_lc or "").lower()
    volatile_markers = [
        "today", "yesterday", "this week", "this month", "breaking", "right now", "as of",
        "just", "announced", "acquired", "resigned", "appointed",
        "current ceo", "current president", "latest",
        "insider information", "based on insider",
    ]
    return any(m in t for m in volatile_markers)


def _looks_like_historical_year_fact(text: str) -> bool:
    """
    Allow stable statements like: "X was ratified in 1788."
    If it's basically a year mention + past tense, treat numeric as non-risky.
    """
    if not text:
        return False
    t = text.strip().lower()
    years = re.findall(r"\b(1[5-9]\d{2}|20\d{2})\b", t)
    if not years:
        return False

    # Past tense / historical cues
    hist_cues = ["was", "were", "ratified", "founded", "established", "signed", "enacted", "born", "died", "published"]
    return any(c in t for c in hist_cues) and len(t.split()) <= 20


def _contains_quantified_numeric_claim(text: str) -> bool:
    """
    Risky numeric = money amounts, % returns, time-bound promises, dosage, etc.
    Not merely "1788".
    """
    if not text:
        return False
    t = text.lower()

    # money or dosage
    if re.search(r"(\$|usd|million|billion|trillion|mg|ml|dose|dosage)\b", t):
        return True

    # % claim
    if "%" in t or re.search(r"\b\d+\s*(percent|percentage)\b", t):
        return True

    # time-bound promise
    if re.search(r"\bwithin\s+\d+\s*(day|days|week|weeks|month|months)\b", t):
        return True

    # Any explicit large number with action language
    if re.search(r"\b\d{4,}\b", t) and any(w in t for w in ["send", "transfer", "wire", "pay"]):
        return True

    return False


def _execution_intent(text_lc: str) -> bool:
    t = (text_lc or "").lower()
    intent_markers = [
        "send", "transfer", "wire", "pay", "approve", "approved by", "execute", "deploy", "run in production",
        "buy", "sell", "place order", "immediately", "right away",
        "write back", "update record", "submit filing", "file this",
    ]
    return any(m in t for m in intent_markers)


def _liability_tier(text_lc: str) -> str:
    t = (text_lc or "").lower()
    if not t:
        return "low"

    high_markers = [
        "dose", "dosage", "mg", "ml", "prescribe", "diagnose", "treat", "contraindication",
        "court", "precedent", "statute", "legal filing", "contract",
        "wire", "transfer", "payment", "treasury", "vendor id",
        "insider information", "sec", "regulatory", "compliance",
    ]
    medium_markers = ["policy", "security", "incident", "breach", "risk", "finance", "healthcare", "law", "audit"]

    if any(m in t for m in high_markers):
        return "high"
    if any(m in t for m in medium_markers):
        return "medium"
    return "low"


def _guardrail(text_lc: str) -> Optional[str]:
    t = (text_lc or "").lower()

    # Insider trading / market abuse
    if "insider information" in t or "based on insider" in t:
        return "insider_information"

    # Direct payment instruction / treasury movement
    payment_markers = ["send $", "wire", "transfer", "corporate treasury", "vendor id", "pay ", "payment"]
    if any(m in t for m in payment_markers):
        return "payment_instruction"

    return None
    # -----------------------------
# Heuristic readiness signal (MVP)
# -----------------------------
def heuristic_readiness_signal(text: str, evidence: str, policy_mode: str):
    """
    Returns:
      readiness_signal (0-100),
      verdict,
      explanation,
      signals,
      references,
      guardrail
    """
    t = (text or "").strip()
    ev = (evidence or "").strip()
    tlc = t.lower()

    urls = extract_urls(ev)
    references = [{"type": "url", "value": u} for u in urls]
    evidence_present = len(references) > 0

    category = _volatility_category(tlc)
    volatile = _is_volatile(tlc)

    liability = _liability_tier(tlc)
    exec_intent = _execution_intent(tlc)

    # numeric risk (exclude simple historical year facts)
    risky_numeric = _contains_quantified_numeric_claim(t)
    historical_year_ok = _looks_like_historical_year_fact(t)

    legal_authority_terms = (
        "held that", "ruled that", "the court found", "precedent",
        "binding authority", "circuit court", "supreme court"
    )
    legal_authority = any(term in tlc for term in legal_authority_terms)

    # Guardrails
    guardrail = _guardrail(tlc)

    risk_flags: List[str] = []
    rules_fired: List[str] = []

    # If guardrail fired, bind deterministically (single source of truth)
    if guardrail:
       risk_flags.append(f"{guardrail}_claim")
       rules_fired.append(f"guardrail_{guardrail}")

    # Risk flags / rules
    if legal_authority:
        risk_flags.append("legal_authority_claim")
        rules_fired.append("legal_authority_flag")
        volatile = True  # authority assertions require evidence

    if exec_intent:
        risk_flags.append("execution_intent_detected")
        rules_fired.append("execution_intent_flag")

    if "$" in tlc or re.search(r"\b\d[\d,]*\b", tlc) and any(w in tlc for w in ["send", "transfer", "wire", "pay"]):
        risk_flags.append("money_amount_claim")
        rules_fired.append("risky_numeric_flag")

    if "%" in tlc or re.search(r"\b\d+\s*(percent|percentage)\b", tlc):
        risk_flags.append("percentage_return_claim")
        rules_fired.append("risky_numeric_flag")

    if re.search(r"\bwithin\s+\d+\s*(day|days|week|weeks|month|months)\b", tlc):
        risk_flags.append("time_bound_promise_claim")
        rules_fired.append("risky_numeric_flag")


    # Evidence required logic (enterprise framing)
    # Require evidence if:
    # - volatile, OR
    # - quantified risky numeric, OR
    # - high liability domain, OR
    # - execution intent
    # BUT do NOT require evidence for stable historical year facts.
    evidence_required_for_allow = (
        volatile
        or (risky_numeric and not historical_year_ok)
        or (liability == "high")
        or exec_intent
        or legal_authority
    )

    # Base readiness signal
    readiness = 55

    # Evidence boosts confidence
    trust_tier = None
    confidence = 0.2
    if evidence_present:
        trust_tier = "B"
        confidence = 0.65
        readiness += 12
        rules_fired.append("evidence_present_bonus")
        risk_flags.append("evidence_present")

    # Volatile without evidence penalty
    if volatile and not evidence_present:
        readiness -= 20
        rules_fired.append("volatile_without_evidence_penalty")

    # Risky numeric without evidence penalty
    if (risky_numeric and not historical_year_ok) and not evidence_present:
        readiness -= 15
        rules_fired.append("risky_numeric_without_evidence_penalty")

    # Exec intent without evidence penalty
    if exec_intent and not evidence_present:
        readiness -= 10
        rules_fired.append("execution_intent_without_evidence_penalty")

    # Guardrail hard floor (still BLOCK later, but makes the signal reflect severity)
    if guardrail:
        readiness = min(readiness, 20)

    readiness = max(0, min(100, int(readiness)))

    # Verdict language (avoid "false" when it's a prohibited action)
    if guardrail:
        verdict = "Unsafe / prohibited to execute"
    else:
        if readiness >= 80:
            verdict = "Likely reliable / consistent"
        elif readiness >= 55:
            verdict = "Unclear / needs verification"
        else:
            verdict = "High risk / unreliable"

    explanation = (
        "MVP reliability gating with volatility + evidence requirements + execution guardrails. "
        "Replace with evidence-backed verification in production."
    )

    signals = {
        "volatility": "VOLATILE" if volatile else "STABLE",
        "volatility_category": category,
        "has_references": evidence_present,
        "reference_count": len(references),
        "evidence_validation_status": "PRESENT" if evidence_present else "MISSING",
        "evidence_trust_tier": trust_tier,
        "evidence_confidence": confidence,
        "evidence_required_for_allow": evidence_required_for_allow,
        "liability_tier": liability,
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail,
    }

    return readiness, verdict, explanation, signals, references, guardrail


def decision_gate(readiness_signal: int, signals: Dict[str, Any], policy_mode: str) -> Tuple[str, str]:
    """
    Canonical enforcement gate.
    Returns: (action, reason)
    """
    guardrail = signals.get("guardrail")
    if guardrail:
        return "BLOCK", f"Blocked by guardrail: {guardrail}."

    requires = bool(signals.get("evidence_required_for_allow"))
    has_refs = bool(signals.get("has_references"))

    if requires and not has_refs:
        return "REVIEW", "Evidence required for volatile, quantified numeric, high-liability, or execution-intent claims under enterprise policy."

    if readiness_signal < 35:
        return "BLOCK", "Blocked: readiness below minimum reliability threshold."

    return "ALLOW", "Approved under enterprise policy."


# -----------------------------
# Demo response shaping (canonical)
# -----------------------------
def shape_demo_response(resp_obj: dict) -> dict:
    """
    Investor-safe / enterprise-safe demo contract.
    Shows the deterministic enforcement artifact WITHOUT leaking internal telemetry.
    """

    raw_decision = resp_obj.get("decision")
    if isinstance(raw_decision, dict):
        decision_obj = {
            "action": raw_decision.get("action") or "REVIEW",
            "reason": raw_decision.get("reason") or "",
        }
    else:
        decision_obj = {
            "action": (raw_decision or "REVIEW"),
            "reason": (resp_obj.get("decision_detail") or {}).get("reason", ""),
        }

    event_id = resp_obj.get("event_id") or resp_obj.get("request_id") or ""
    audit_sha = (
        resp_obj.get("audit_fingerprint_sha256")
        or (resp_obj.get("audit_fingerprint") or {}).get("sha256")
        or ""
    )

    # ---- Signals: redact internals, keep only contract-relevant fields ----
    sig = resp_obj.get("signals") or {}
    public_signals = {
        "volatility": sig.get("volatility"),
        "evidence_validation_status": sig.get("evidence_validation_status"),
        "evidence_confidence": sig.get("evidence_confidence"),
        "has_references": sig.get("has_references"),
        "reference_count": sig.get("reference_count"),
    }

    # Optional: boolean summaries (do not leak rule names)
    lt = (sig.get("liability_tier") or "").lower()
    public_signals["high_liability"] = (lt == "high")

    # Coarse execution intent boolean (if you already compute it)
    rf = sig.get("risk_flags") or []
    public_signals["execution_intent"] = any(
        str(x).lower() in ("execution_intent_detected", "execution_intent")
        for x in rf
    )

    shaped = {
        "contract": {
            "name": "TruCite Runtime Execution Reliability",
            "contract_version": DEMO_CONTRACT_VERSION,
            "schema_version": resp_obj.get("schema_version"),
            "request_id": event_id,
        },

        "decision": decision_obj,
        "decision_action": decision_obj.get("action"),

        # keep one public readiness metric
        "readiness_signal": resp_obj.get("readiness_signal", resp_obj.get("score")),
        "verdict": resp_obj.get("verdict"),

        "policy": {
            "mode": resp_obj.get("policy_mode"),
            "version": resp_obj.get("policy_version"),
            "hash": resp_obj.get("policy_hash"),
        },

        "audit": {
            "event_id": event_id,
            "audit_fingerprint_sha256": audit_sha,
        },

        "latency_ms": resp_obj.get("latency_ms"),

        # Keep evidence list (URLs) if present; OK for demo
        "references": resp_obj.get("references", []),

        # Public-safe signals only
        "signals": public_signals,

        # Guardrail as a public reason label is OK (no internals)
        "guardrail": resp_obj.get("guardrail", None),

        "execution_boundary": resp_obj.get("execution_boundary", False),
        "execution_commit": resp_obj.get("execution_commit", {}),
        "explanation": resp_obj.get("explanation", ""),
    }

    return shaped
@app.route("/", methods=["GET"])
def root():
    try:
        return send_from_directory(app.static_folder, "index.html")
    except Exception:
        return jsonify({
            "name": "TruCite Backend",
            "status": "ok",
            "endpoints": ["/api/runtime", "/api/score", "/health"],
            "policy_version": POLICY_VERSION,
            "schema_version": SCHEMA_VERSION,
        }), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "trucite-backend",
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "default_policy_mode": DEFAULT_POLICY_MODE,
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }), 200


def _handle_runtime_request():
    if request.method == "OPTIONS":
        return ("", 204)

    start = time.time()

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()
    policy_mode = (payload.get("policy_mode") or DEFAULT_POLICY_MODE).strip().lower()

    if not text:
        return json_error("MISSING_TEXT", "Missing 'text' in request body", 400)

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    readiness, verdict, explanation, signals, references, guardrail = heuristic_readiness_signal(
        text=text,
        evidence=evidence,
        policy_mode=policy_mode,
    )

    # Decision gate (action, reason)
    action, reason = decision_gate(
        int(readiness),
        signals,
        policy_mode=policy_mode,
    )

    # Normalize verdict to action (avoid mismatches)
    if action == "ALLOW":
        verdict = "Likely reliable / consistent"
    elif action == "REVIEW":
        verdict = "Unclear / needs verification"
    elif action == "BLOCK":
        verdict = "Unsafe / prohibited to execute" if signals.get("guardrail") else "High risk / unreliable"

    latency_ms = max(5, int((time.time() - start) * 1000))

    resp_obj = {
        "schema_version": SCHEMA_VERSION,
        "request_id": event_id,
        "latency_ms": latency_ms,

        "verdict": verdict,
        "readiness_signal": int(readiness),
        "decision": {"action": action, "reason": reason},

        "policy_mode": policy_mode,
        "policy_version": POLICY_VERSION,
        "policy_hash": policy_hash(policy_mode),

        "event_id": event_id,
        "audit_fingerprint_sha256": sha,
        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},

        "references": references,
        "signals": signals,
        "explanation": explanation,

        "execution_boundary": (action == "ALLOW"),
        "execution_commit": {
            "authorized": (action == "ALLOW"),
            "action": action,
            "event_id": event_id,
            "policy_hash": policy_hash(policy_mode),
            "audit_fingerprint_sha256": sha,
        },
    }

    if DEMO_MODE:
        return jsonify(shape_demo_response(resp_obj)), 200

    return jsonify(resp_obj), 200


# Preferred product endpoint (what your UI should point to)
@app.route("/api/runtime", methods=["POST", "OPTIONS"])
def api_runtime():
    try:
        return _handle_runtime_request()
    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR missing helper above this section.",
        )


# Backward-compatible endpoint (keep for now)
@app.route("/api/score", methods=["POST", "OPTIONS"])
def api_score():
    try:
        return _handle_runtime_request()
    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR missing helper above this section.",
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
