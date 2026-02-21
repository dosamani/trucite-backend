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
# CORS
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
# Category + liability helpers
# -----------------------------
def _volatility_category(t: str) -> str:
    tl = (t or "").lower()

    # Security / compliance
    if any(k in tl for k in ["iso 27001", "soc 2", "soc2", "nist", "hipaa", "gdpr", "isms", "infosec", "security management"]):
        return "SECURITY"

    if any(k in tl for k in ["diagnosis", "treatment", "medication", "dose", "dosage", "clinical", "patient"]):
        return "MEDICAL"

    if any(k in tl for k in ["court", "statute", "legal", "regulation", "lawsuit", "precedent"]):
        return "LEGAL"

    if any(k in tl for k in ["earnings", "revenue", "stock", "market", "ipo", "insider", "return", "%", "billion", "million"]):
        return "FINANCIAL"

    if any(k in tl for k in ["resigned", "announced", "today", "yesterday", "breaking", "acquired"]):
        return "NEWS"

    # Operational execution commands
    if any(k in tl for k in ["send", "transfer", "wire", "pay", "approve", "execute", "immediately", "treasury", "vendor id"]):
        return "OPERATIONS"

    return "GENERAL"


def _liability_tier(text_lc: str) -> str:
    if not text_lc:
        return "low"

    high_markers = [
        # Medical
        "dose", "dosage", "mg", "ml", "units", "prescribe", "diagnose", "treat",
        "clinical", "patient", "contraindication",
        # Legal
        "legal", "lawsuit", "contract", "filing", "court", "precedent", "statute",
        # Financial / payments
        "wire", "transfer", "payment", "send", "treasury", "bank", "loan", "interest rate",
        "fraud", "sec", "insider",
        # Ops execution
        "delete", "drop", "terminate", "revoke", "approve", "release funds"
    ]
    medium_markers = ["policy", "security", "incident", "breach", "risk", "finance", "healthcare", "law", "compliance"]

    if any(m in text_lc for m in high_markers):
        return "high"
    if any(m in text_lc for m in medium_markers):
        return "medium"
    return "low"


def _claim_type(text_lc: str) -> str:
    if not text_lc:
        return "other"

    volatile_markers = [
        "today", "yesterday", "this week", "this month", "breaking",
        "just", "right now", "as of", "latest", "immediately",
        "resigned", "appointed", "announced", "acquired",
        "lawsuit", "recall", "downgrade", "upgrade", "earnings", "sec",
        "insider", "rumor", "unconfirmed", "leak",
        "within", "in the next",
    ]
    if any(m in text_lc for m in volatile_markers):
        return "volatile"

    # numeric-ish, but not mere IDs
    numeric_markers = ["%", "million", "billion", "trillion", "usd", "$", "€", "£", "mg", "ml", "dose", "dosage"]
    has_digit = any(ch.isdigit() for ch in text_lc)
    if has_digit and any(m in text_lc for m in numeric_markers):
        return "numeric"

    return "stable"
    # -----------------------------
# Risk detection helpers
# -----------------------------
def _detect_risky_numeric(text: str) -> Dict[str, bool]:
    """
    Only treat numeric as 'risky' when it implies money/returns/dose/time-bound promise.
    Prevents ISO 27001 / years like 1788 / IDs from always requiring evidence.
    """
    tl = (text or "").lower()

    has_money = bool(re.search(r"(\$|usd|eur|gbp|€|£)\s*\d", tl)) or bool(re.search(r"\b\d{1,3}(,\d{3})+\b", tl))
    has_percent = "%" in tl or bool(re.search(r"\b\d+(\.\d+)?\s*%\b", tl))
    has_dose = bool(re.search(r"\b\d+(\.\d+)?\s*(mg|ml|mcg|g|units)\b", tl)) or ("dose" in tl or "dosage" in tl)
    has_time_horizon = bool(re.search(r"\b(within|in)\s+\d+\s+(day|days|week|weeks|month|months|hour|hours)\b", tl))

    # Historic/definition years should not be "risky numeric"
    is_historic_year = bool(re.search(r"\b(1[6-9]\d{2}|20\d{2})\b", tl)) and any(k in tl for k in ["ratified", "founded", "born", "in "])

    risky_numeric = (has_money or has_percent or has_dose or has_time_horizon) and not is_historic_year

    return {
        "has_money": has_money,
        "has_percent": has_percent,
        "has_dose": has_dose,
        "has_time_horizon": has_time_horizon,
        "is_historic_year": is_historic_year,
        "risky_numeric": risky_numeric,
    }


def _detect_execution_intent(text: str) -> bool:
    tl = (text or "").lower()
    exec_markers = [
        "send", "transfer", "wire", "pay", "release funds", "execute payment",
        "delete", "drop", "terminate", "revoke", "disable", "approve",
        "immediately", "asap", "right away",
        "corporate treasury", "vendor id", "bank account"
    ]
    return any(m in tl for m in exec_markers)


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
    tl = t.lower()

    urls = extract_urls(ev)
    references = [{"type": "url", "value": u} for u in urls]
    evidence_present = len(references) > 0

    # Trust tier heuristic (demo)
    trust_tier = None
    confidence = 0.2
    if evidence_present:
        trust_tier = "B"
        confidence = 0.65
        if any(("sec.gov" in r["value"].lower() or "nih.gov" in r["value"].lower() or "who.int" in r["value"].lower()) for r in references):
            trust_tier = "A"
            confidence = 0.85

    risk_flags: List[str] = []
    rules_fired: List[str] = []
    guardrail = None

    category = _volatility_category(t)
    liability = _liability_tier(tl)

    # Volatility heuristic
    volatile_triggers = (
        "ceo", "cfo", "current", "today", "right now", "as of", "latest",
        "breaking", "announced", "acquired", "resigned",
        "insider", "leak", "rumor", "unconfirmed",
        "within", "in the next", "immediately", "guaranteed"
    )
    is_volatile = any(k in tl for k in volatile_triggers)

    # Legal authority escalation (case-law authority claims)
    legal_authority_terms = (
        "held that",
        "ruled that",
        "the court found",
        "precedent",
        "binding authority",
        "circuit court",
        "supreme court",
        "controlling authority",
        "the statute provides",
    )
    if any(term in tl for term in legal_authority_terms):
        is_volatile = True
        risk_flags.append("legal_authority_claim")
        rules_fired.append("legal_authority_flag")

    # Risky numeric detection
    num = _detect_risky_numeric(t)
    risky_numeric = num["risky_numeric"]
    if risky_numeric:
        rules_fired.append("risky_numeric_flag")
        if num["has_money"]:
            risk_flags.append("money_amount_claim")
        if num["has_percent"]:
            risk_flags.append("percentage_return_claim")
        if num["has_dose"]:
            risk_flags.append("dose_amount_claim")
        if num["has_time_horizon"]:
            risk_flags.append("time_bound_promise_claim")
    else:
        if any(ch.isdigit() for ch in tl):
            rules_fired.append("identifier_number_present")

    # Insider information guardrail (test #7)
    if "insider information" in tl or ("based on insider" in tl):
        guardrail = "insider_information"
        risk_flags.append("insider_information_claim")
        rules_fired.append("guardrail_insider_information")

    # Execution intent guardrail (test #8)
    execution_intent = _detect_execution_intent(t)
    if execution_intent:
        risk_flags.append("execution_intent_detected")
        rules_fired.append("execution_intent_flag")

        if any(k in tl for k in ["send", "transfer", "wire", "pay", "treasury", "vendor id", "bank"]) and (num["has_money"] or "$" in tl):
            guardrail = "payment_instruction"
            risk_flags.append("payment_instruction")
            rules_fired.append("guardrail_payment_instruction")

    # Evidence requirement policy
    evidence_required_for_allow = bool(
        is_volatile
        or risky_numeric
        or (liability == "high" and category in ("LEGAL", "MEDICAL", "FINANCIAL", "SECURITY", "OPERATIONS"))
        or execution_intent
    )

    # Readiness scoring
    readiness = 55

    if evidence_present:
        readiness += 18
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    if trust_tier == "A":
        readiness += 5
        rules_fired.append("high_trust_source_bonus")

    if is_volatile and not evidence_present:
        readiness -= 15
        rules_fired.append("volatile_without_evidence_penalty")

    if risky_numeric and not evidence_present:
        readiness -= 15
        rules_fired.append("risky_numeric_without_evidence_penalty")

    if guardrail:
        readiness = min(readiness, 20)

    readiness = max(0, min(100, int(readiness)))

    if readiness >= 80:
        verdict = "Likely true / consistent"
    elif readiness >= 60:
        verdict = "Some risk / needs review"
    else:
        verdict = "High risk / unreliable"

    explanation = "MVP reliability gating with volatility + evidence requirements + execution guardrails. Replace with evidence-backed verification in production."

    signals = {
        "volatility": "VOLATILE" if is_volatile else "STABLE",
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

    return readiness, verdict, explanation, signals, references


def decision_gate(readiness_signal: int, signals: Dict[str, Any], policy_mode: str) -> Tuple[str, str]:
    guardrail = signals.get("guardrail")
    if guardrail:
        return "BLOCK", f"Blocked by guardrail: {guardrail}."

    requires = bool(signals.get("evidence_required_for_allow"))
    has_refs = bool(signals.get("has_references"))

    if requires and not has_refs:
        return "REVIEW", "Evidence required for volatile, quantified numeric, high-liability, or execution-intent claims under enterprise policy."

    if readiness_signal < 35:
        return "BLOCK", "Readiness below minimum reliability threshold."

    return "ALLOW", "Approved under enterprise policy."


def shape_demo_response(resp_obj: dict) -> dict:
    raw_decision = resp_obj.get("decision")
    if isinstance(raw_decision, dict):
        decision_obj = raw_decision
    else:
        decision_obj = {
            "action": (raw_decision or "REVIEW"),
            "reason": (resp_obj.get("decision_detail") or {}).get("reason", ""),
        }

    event_id = resp_obj.get("event_id") or resp_obj.get("request_id") or ""
    audit_sha = resp_obj.get("audit_fingerprint_sha256") or ""

    shaped = {
        "contract": {
            "name": "TruCite Runtime Execution Reliability",
            "contract_version": DEMO_CONTRACT_VERSION,
            "schema_version": resp_obj.get("schema_version"),
            "request_id": event_id,
        },
        "decision": decision_obj,
        "decision_action": decision_obj.get("action"),
        "readiness_signal": resp_obj.get("readiness_signal"),
        "score": resp_obj.get("score"),  # backward compat
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
            "endpoints": ["/api/evaluate", "/api/score", "/health"],
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


# Keep /api/score for backward compatibility, but make /api/evaluate the primary
@app.route("/api/evaluate", methods=["POST", "OPTIONS"])
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

        readiness, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        action, reason = decision_gate(int(readiness), signals, policy_mode=policy_mode)

        if action == "ALLOW":
            verdict = "Likely true / consistent"
        elif action == "REVIEW":
            verdict = "Unclear / needs verification"
        else:
            verdict = "Likely false / unsafe to execute"

        latency_ms = max(5, int((time.time() - start) * 1000))
        text_lc = text.lower()

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,
            "readiness_signal": int(readiness),
            "score": int(readiness),  # backward compat for existing UI

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
