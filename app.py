import os
import re
import time
import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from flask import Flask, jsonify, request, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -----------------------------
# Constants / Config
# -----------------------------
SCHEMA_VERSION = "2.0"
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
DEMO_CONTRACT_VERSION = "2026.01"

# Demo mode: deterministic + screenshot-friendly behavior
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
    payload = {
        "error_code": code,
        "message": message,
    }
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

    # De-dupe while preserving order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


# -----------------------------
# Claim profiling helpers (MVP)
# -----------------------------
def _claim_type(text_lc: str) -> str:
    """
    Very lightweight claim-type heuristic for MVP demos.
    Returns: stable | volatile | numeric | other
    """
    if not text_lc:
        return "other"

    volatile_markers = [
        "today", "yesterday", "this week", "this month", "breaking",
        "right now", "as of", "latest", "just",
        "resigned", "appointed", "announced", "acquired",
        "lawsuit", "recall", "downgrade", "upgrade", "earnings", "sec",
        "ceo", "cfo", "chairman", "insider information",
        "within ", "in the next", "immediately"
    ]
    if any(m in text_lc for m in volatile_markers):
        return "volatile"

    # numeric-ish claims (broad bucket)
    numeric_markers = ["%", "million", "billion", "trillion", "usd", "$", "mg", "ml", "dose", "dosage", "within", "days", "weeks", "months"]
    has_digit = any(ch.isdigit() for ch in text_lc)
    if has_digit and any(m in text_lc for m in numeric_markers):
        return "numeric"

    return "stable"


def _liability_tier(text_lc: str) -> str:
    """
    MVP liability heuristic.
    Returns: high | medium | low
    """
    if not text_lc:
        return "low"

    high_markers = [
        # medical
        "dose", "dosage", "mg", "ml", "prescribe", "diagnose", "treat", "contraindication",
        "clinical", "patient", "surgery",

        # legal
        "legal", "lawsuit", "contract", "filing", "court", "precedent", "binding authority",

        # finance / payments
        "wire", "transfer", "send $", "send ", "payment", "corporate treasury", "vendor id", "bank", "loan", "interest rate",
        "insider information", "buy ", "sell ", "stock", "increase", "returns",

        # regulatory / compliance
        "regulatory", "compliance", "sec", "aml", "kyc",
    ]

    medium_markers = [
        # security/compliance
        "security", "infosec", "iso ", "soc", "nist", "27001", "isms", "audit", "controls",
        # general risk markers
        "policy", "incident", "breach", "risk", "finance", "healthcare", "law"
    ]

    if any(m in text_lc for m in high_markers):
        return "high"
    if any(m in text_lc for m in medium_markers):
        return "medium"
    return "low"


def _volatility_category(text_lc: str) -> str:
    """
    Expanded category routing to prevent mislabels observed in tests (#8/#10).
    """
    t = (text_lc or "").lower()

    # SECURITY / COMPLIANCE
    if any(k in t for k in [
        "iso 27001", "iso27001", "isms", "information security management",
        "soc 2", "soc2", "nist", "27001", "security management",
        "infosec", "information security", "controls", "audit"
    ]):
        return "SECURITY"

    # MEDICAL
    if any(k in t for k in ["diagnosis", "treatment", "medication", "dose", "dosage", "mg", "ml", "clinical", "patient"]):
        return "MEDICAL"

    # LEGAL
    if any(k in t for k in ["court", "statute", "legal", "regulation", "lawsuit", "precedent", "binding authority"]):
        return "LEGAL"

    # FINANCIAL (includes trades + payments)
    if any(k in t for k in ["earnings", "revenue", "stock", "market", "ipo", "insider information", "buy ", "sell "]):
        return "FINANCIAL"
    if any(k in t for k in ["wire", "transfer", "send $", "corporate treasury", "vendor id", "payment"]):
        return "FINANCIAL"

    # NEWS
    if any(k in t for k in ["resigned", "announced", "today", "yesterday", "breaking", "as of", "latest"]):
        return "NEWS"

    return "GENERAL"
    # -----------------------------
# Scoring / signals
# -----------------------------
def _is_standard_identifier(text_lc: str) -> bool:
    """
    Detects common non-quantified numeric identifiers (ISO 27001, RFC 9110, etc.)
    so we don't treat them as "numeric claims" requiring evidence.
    """
    t = (text_lc or "")
    patterns = [
        r"\biso\s?\d{3,5}\b",
        r"\biec\s?\d{3,5}\b",
        r"\brfc\s?\d{3,5}\b",
        r"\bnist\s?[a-z0-9\-]{2,}\b",
        r"\bsoc\s?2\b",
    ]
    return any(re.search(p, t, flags=re.IGNORECASE) for p in patterns)


def _is_historical_year_fact(text_lc: str) -> bool:
    """
    If the only numeric is a year-like token, treat as stable/historical fact.
    This prevents demo from forcing REVIEW on benign statements like "ratified in 1788".
    """
    if not text_lc:
        return False

    years = re.findall(r"\b(1[5-9]\d{2}|20\d{2}|2100)\b", text_lc)
    if not years:
        return False

    # If it contains prediction/finance/medical/payment markers, it isn't a benign year fact
    disqualifiers = ["%", "$", "mg", "ml", "dose", "within", "days", "weeks", "months", "buy ", "sell ", "wire", "transfer", "payment"]
    if any(d in text_lc for d in disqualifiers):
        return False

    return True


def _is_quantified_numeric_claim(text_lc: str) -> bool:
    """
    Numeric claim that should require evidence:
    - money amounts, percentages, time horizons, dosages, growth claims, etc.
    """
    if not text_lc or not any(ch.isdigit() for ch in text_lc):
        return False

    # Exclusions (identifiers, standards, benign years)
    if _is_standard_identifier(text_lc) or _is_historical_year_fact(text_lc):
        return False

    quantified_markers = [
        "%", "$", "usd", "million", "billion", "trillion",
        "mg", "ml", "dose", "dosage",
        "within", "in the next", "days", "weeks", "months", "hours",
        "increase", "decrease", "growth", "returns", "roi"
    ]
    return any(m in text_lc for m in quantified_markers)


def _detect_guardrails(text_lc: str) -> Tuple[str, List[str]]:
    """
    Hard guardrails that should BLOCK in enterprise execution contexts.
    Returns: (guardrail_name_or_None, additional_risk_flags)
    """
    if not text_lc:
        return None, []

    flags = []

    # Payment / treasury transfer instruction
    if any(k in text_lc for k in ["send $", "wire", "transfer", "corporate treasury", "vendor id", "approved by cfo"]):
        flags.append("payment_instruction")
        return "PAYMENT_EXECUTION_INSTRUCTION", flags

    # Insider trading / market manipulation instruction
    if ("insider information" in text_lc) or (("buy " in text_lc or "sell " in text_lc) and ("within" in text_lc or "%" in text_lc or "increase" in text_lc)):
        flags.append("insider_trading_instruction")
        return "INSIDER_TRADING_RISK", flags

    return None, flags


def heuristic_score(text: str, evidence: str, policy_mode: str):
    """
    Returns:
        readiness_signal (0-100),
        verdict string,
        explanation string,
        signals dict,
        references list[{type,value}]
    """
    t = (text or "").strip()
    ev = (evidence or "").strip()
    tlc = t.lower()
    elc = ev.lower()

    urls = extract_urls(ev)
    references = [{"type": "url", "value": u} for u in urls]

    evidence_present = len(references) > 0

    # Volatility heuristic (expanded)
    volatile_triggers = (
        "ceo", "cfo", "current", "today", "right now", "as of", "latest",
        "president", "prime minister",
        "within", "in the next", "immediately",
        "announced", "acquired", "resigned", "breaking",
        "insider information"
    )
    is_volatile = any(k in tlc for k in volatile_triggers)

    # Numeric claim heuristic (refined)
    quantified_numeric = _is_quantified_numeric_claim(tlc)

    # Legal authority escalation
    risk_flags: List[str] = []
    rules_fired: List[str] = []

    legal_authority_terms = (
        "held that",
        "ruled that",
        "the court found",
        "precedent",
        "binding authority",
        "circuit court",
        "supreme court"
    )
    if any(term in tlc for term in legal_authority_terms):
        is_volatile = True
        risk_flags.append("legal_authority_claim")
        rules_fired.append("legal_authority_flag")

    # Guardrails (hard blocks)
    guardrail, guardrail_flags = _detect_guardrails(tlc)
    if guardrail:
        risk_flags.extend(guardrail_flags)
        rules_fired.append("guardrail_flag")

    # Category + liability tier
    vol_category = _volatility_category(tlc)
    liability = _liability_tier(tlc)

    # Evidence required for ALLOW:
    # - volatile claims, quantified numeric claims, OR high-liability domains
    evidence_required_for_allow = bool(is_volatile or quantified_numeric or (liability == "high"))

    # Trust tier heuristic (demo)
    trust_tier = None
    confidence = 0.2
    if evidence_present:
        trust_tier = "B"
        confidence = 0.65
        # bump for well-known primary domains (demo)
        if any("apple.com" in r["value"].lower() for r in references):
            trust_tier = "A"
            confidence = 0.85

    # Misc flags
    if len(t.split()) <= 6:
        risk_flags.append("short_declarative_claim")
        rules_fired.append("short_declarative_bonus")

    if evidence_present:
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    if is_volatile:
        rules_fired.append("volatile_claim_flag")

    if quantified_numeric:
        rules_fired.append("numeric_claim_flag")

    # Readiness signal construction (demo)
    readiness = 58

    if evidence_present:
        readiness += 18
    if trust_tier == "A":
        readiness += 5

    # Penalties
    if is_volatile and not evidence_present:
        readiness -= 15
    if quantified_numeric and not evidence_present:
        readiness -= 15
    if liability == "high" and not evidence_present:
        readiness -= 10

    # Guardrail penalty (will BLOCK anyway, but keep signal intuitive)
    if guardrail:
        readiness -= 25

    readiness = max(0, min(100, int(readiness)))

    # Verdict bands
    if readiness >= 80:
        verdict = "Likely true / consistent"
    elif readiness >= 60:
        verdict = "Some risk / needs review"
    else:
        verdict = "High risk / unreliable"

    explanation = "MVP heuristic reliability gating with volatility + evidence requirements. Replace with evidence-backed verification in production."

    signals = {
        "volatility": "VOLATILE" if is_volatile else "STABLE",
        "volatility_category": vol_category,
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

    return readiness, verdict, explanation, signals, references
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
        return "REVIEW", "Evidence required for volatile, quantified numeric, or high-liability claim under enterprise policy."

    if readiness_signal < 35:
        return "BLOCK", "Readiness below minimum reliability threshold."

    return "ALLOW", "Approved under enterprise policy."


# ---- DEMO response shaping ----
def shape_demo_response(resp_obj: dict) -> dict:
    """
    Produces a clean, investor-facing response while keeping internal scoring intact.
    Ensures decision is always an object: {"action": "...", "reason": "..."}
    """
    raw_decision = resp_obj.get("decision")
    if isinstance(raw_decision, dict):
        decision_obj = raw_decision
    else:
        decision_obj = {
            "action": (raw_decision or "REVIEW"),
            "reason": resp_obj.get("decision_detail", {}).get("reason", "") if isinstance(resp_obj.get("decision_detail"), dict) else ""
        }

    event_id = resp_obj.get("event_id") or resp_obj.get("request_id") or ""
    audit_sha = (
        resp_obj.get("audit_fingerprint_sha256")
        or (resp_obj.get("audit_fingerprint") or {}).get("sha256")
        or ""
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

        # Primary term (anti-"scoring" language)
        "readiness_signal": resp_obj.get("readiness_signal"),

        # Keep legacy for compatibility (frontend may still read data.score)
        "score": resp_obj.get("score"),

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
        "references": resp_obj.get("references", []),
        "signals": resp_obj.get("signals", {}),
        "explanation": resp_obj.get("explanation", ""),

        "execution_boundary": resp_obj.get("execution_boundary", False),
        "execution_commit": resp_obj.get("execution_commit", {}),
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
            "endpoints": ["/api/score", "/health"],
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


# ---- API: /api/score ----
@app.route("/api/score", methods=["POST", "OPTIONS"])
def api_score():
    try:
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

        claims = [{"text": text}]

        readiness_signal, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        # Decision gate
        action, reason = decision_gate(
            int(readiness_signal),
            signals,
            policy_mode=policy_mode,
        )

        # Normalize verdict to decision (prevents mismatch)
        if action == "ALLOW":
            verdict = "Likely true / consistent"
        elif action == "REVIEW":
            verdict = "Unclear / needs verification"
        elif action == "BLOCK":
            verdict = "Likely unsafe / blocked"

        latency_ms = max(5, int((time.time() - start) * 1000))
        text_lc = text.lower()

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,

            # Primary field
            "readiness_signal": int(readiness_signal),

            # Legacy (compat)
            "score": int(readiness_signal),

            "decision": {"action": action, "reason": reason},

            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(policy_mode),

            "event_id": event_id,
            "audit_fingerprint_sha256": sha,
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},

            "claims": claims,
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

            "claim_profile": {
                "claim_type": _claim_type(text_lc),
                "liability_tier": _liability_tier(text_lc),
                "regulatory_context": "auto",
            },
        }

        if DEMO_MODE:
            return jsonify(shape_demo_response(resp_obj)), 200

        return jsonify(resp_obj), 200

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
