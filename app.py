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

# Demo mode: keep deterministic + screenshot-friendly behavior
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

def make_request_id(text: str, evidence: str, policy_mode: str) -> str:
    """
    Deterministic request id so same input yields same id (demo-friendly).
    """
    base = f"{(text or '').strip()}||{(evidence or '').strip()}||{(policy_mode or DEFAULT_POLICY_MODE).strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]

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


def heuristic_score(text: str, evidence: str, policy_mode: str):
    """
    Returns:
        score (0-100),
        verdict string,
        explanation string,
        signals dict,
        references list[{type,value}]
    """
    t = (text or "").strip()
    ev = (evidence or "").strip()

    urls = extract_urls(ev)
    references = [{"type": "url", "value": u} for u in urls]

    # Very simple volatility heuristic (demo)
    volatile_triggers = ("ceo", "current", "today", "right now", "as of", "latest", "president", "prime minister")
    is_volatile = any(k in t.lower() for k in volatile_triggers)

    # Numeric claim heuristic
    has_number = bool(re.search(r"\d", t))

    evidence_present = len(references) > 0

    evidence_required_for_allow = is_volatile or has_number

    # Simple trust tier heuristic
    trust_tier = None
    confidence = 0.2
    if evidence_present:
        trust_tier = "B"
        confidence = 0.65
        # bump for well-known primary domains (demo)
        if any("apple.com" in r["value"].lower() for r in references):
            trust_tier = "A"
            confidence = 0.85

    risk_flags: List[str] = []
    rules_fired: List[str] = []

    if len(t.split()) <= 6:
        risk_flags.append("short_declarative_claim")
        rules_fired.append("short_declarative_bonus")

    if evidence_present:
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    if is_volatile:
        rules_fired.append("volatile_claim_flag")

    if has_number:
        rules_fired.append("numeric_claim_flag")

    # Score construction (demo)
    score = 55
    if evidence_present:
        score += 18
    if trust_tier == "A":
        score += 5
    if is_volatile and not evidence_present:
        score -= 15
    if has_number and not evidence_present:
        score -= 15

    score = max(0, min(100, int(score)))

    if score >= 80:
        verdict = "Likely true / consistent"
    elif score >= 60:
        verdict = "Some risk / needs review"
    else:
        verdict = "High risk / unreliable"

    explanation = "MVP heuristic scoring with volatility + evidence gating. Replace with evidence-backed verification in production."

    signals = {
        "volatility": "VOLATILE" if is_volatile else "STABLE",
        "volatility_category": "",
        "has_references": evidence_present,
        "reference_count": len(references),
        "evidence_validation_status": "PRESENT" if evidence_present else "MISSING",
        "evidence_trust_tier": trust_tier,
        "evidence_confidence": confidence,
        "evidence_required_for_allow": evidence_required_for_allow,
        "liability_tier": "low",
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": None,
    }

    return score, verdict, explanation, signals, references

def decision_gate(score: int, signals: Dict[str, Any], policy_mode: str) -> Tuple[str, str]:
    """
    Canonical enforcement gate.
    Returns: (action, reason)
    """
    requires = bool(signals.get("evidence_required_for_allow"))
    has_refs = bool(signals.get("has_references"))

    if requires and not has_refs:
        return "REVIEW", "Evidence required for volatile/numeric claim under enterprise policy."

    if score < 40:
        return "BLOCK", "Score below minimum reliability threshold."

    return "ALLOW", "Approved under enterprise policy."
    
# ---- DEMO response shaping ----

DEMO_MODE = True
DEMO_CONTRACT_VERSION = "2026.01"

def shape_demo_response(resp_obj: dict) -> dict:
    """
    Produces a clean, investor-facing response while keeping
    internal scoring intact.

    Canonical output:
      - contract
      - decision (object)
      - policy
      - audit
      - references
      - signals
    """

    # --- Normalize decision (dict OR legacy string) ---
    raw_decision = resp_obj.get("decision")

    if isinstance(raw_decision, dict):
        decision_obj = raw_decision
    else:
        decision_obj = {
            "action": (raw_decision or "REVIEW"),
            "reason": (resp_obj.get("decision_detail") or {}).get("reason", "")
        }

    # --- Deterministic ids for demo contract/audit ---
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

        # Canonical decision
        "decision": decision_obj,
        "decision_action": decision_obj.get("action"),
        "score": resp_obj.get("score"),
        "verdict": resp_obj.get("verdict"),

        # Policy metadata
        "policy": {
            "mode": resp_obj.get("policy_mode"),
            "version": resp_obj.get("policy_version"),
            "hash": resp_obj.get("policy_hash"),
        },

        # Audit artifact
        "audit": {
            "event_id": event_id,
            "audit_fingerprint_sha256": audit_sha,
        },

        # Runtime metrics
        "latency_ms": resp_obj.get("latency_ms"),

        # Evidence
        "references": resp_obj.get("references", []),

        # Signals + explanation
        "signals": resp_obj.get("signals", {}),
        "explanation": resp_obj.get("explanation", ""),
    }

    return shaped

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
            "action": raw_decision,
            "reason": resp_obj.get("decision_detail", {}).get("reason"),
        }

    shaped = {
        "contract": {
            "name": "TruCite Runtime Execution Reliability",
            "contract_version": DEMO_CONTRACT_VERSION,
            "schema_version": resp_obj.get("schema_version"),
        },
        "decision": decision_obj,
        "decision_action": decision_obj.get("action"),
        "score": resp_obj.get("score"),
        "verdict": resp_obj.get("verdict"),
        "policy": {
            "mode": resp_obj.get("policy_mode"),
            "version": resp_obj.get("policy_version"),
            "hash": resp_obj.get("policy_hash"),
        },
        "audit": {
            "event_id": resp_obj.get("event_id"),
            "audit_fingerprint_sha256": resp_obj.get("audit_fingerprint_sha256"),
        },
        "latency_ms": resp_obj.get("latency_ms"),
        "references": resp_obj.get("references", []),
        "signals": resp_obj.get("signals", {}),
        "explanation": resp_obj.get("explanation", ""),
        "execution_boundary": resp_obj.get("execution_boundary", False),
        "execution_commit": resp_obj.get("execution_commit", {
          "authorized": False,
          "action": None,
          "event_id": None,
          "policy_hash": None,
          "audit_fingerprint_sha256": None
        }),
    }

    return shaped


@app.route("/", methods=["GET"])
def root():
    # If you have /static/index.html, serve it; otherwise return JSON
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

    # Volatile / time-sensitive language
    volatile_markers = [
        "today", "yesterday", "this week", "this month", "breaking",
        "just", "right now", "resigned", "appointed", "announced", "acquired",
        "lawsuit", "recall", "downgrade", "upgrade", "earnings", "sec",
        "ceo", "cfo", "chairman",
    ]
    if any(m in text_lc for m in volatile_markers):
        return "volatile"

    # Numeric-ish claims (very simple)
    numeric_markers = ["%", "million", "billion", "trillion", "usd", "$", "mg", "ml", "dose", "dosage"]
    has_digit = any(ch.isdigit() for ch in text_lc)
    if has_digit or any(m in text_lc for m in numeric_markers):
        return "numeric"

    # Default
    return "stable"


def _liability_tier(text_lc: str) -> str:
    """
    MVP liability heuristic.
    Returns: high | medium | low
    """
    if not text_lc:
        return "low"

    high_markers = [
        "dose", "dosage", "mg", "ml", "prescribe", "diagnose", "treat",
        "clinical", "patient", "surgery", "contraindication",
        "legal", "lawsuit", "contract", "filing", "court", "precedent",
        "wire", "transfer", "payment", "bank", "loan", "interest rate",
        "fraud", "regulatory", "compliance", "sec",
    ]
    medium_markers = ["policy", "security", "incident", "breach", "risk", "finance", "healthcare", "law"]

    if any(m in text_lc for m in high_markers):
        return "high"
    if any(m in text_lc for m in medium_markers):
        return "medium"
    return "low"

def _volatility_category(t: str) -> str:
    t = t.lower()

    if any(k in t for k in ["diagnosis", "treatment", "medication", "dose", "clinical"]):
        return "MEDICAL"

    if any(k in t for k in ["court", "statute", "legal", "regulation", "lawsuit"]):
        return "LEGAL"

    if any(k in t for k in ["earnings", "revenue", "stock", "market", "ipo"]):
        return "FINANCIAL"

    if any(k in t for k in ["resigned", "announced", "today", "yesterday", "breaking"]):
        return "NEWS"

    return "GENERAL"


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

        # Fingerprint / Event ID
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        event_id = sha[:12]
        ts = datetime.now(timezone.utc).isoformat()

        # Claims (MVP: single-claim passthrough)
        claims = [{"text": text}]

        # Scoring
        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        signals["volatility_category"] = _volatility_category(text.lower())

        # Decision gate (must return action, reason)
        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

        # Normalize verdict to decision (prevents ALLOW + "needs review" mismatch)
        if action == "ALLOW":
            verdict = "Likely true / consistent"
        elif action == "REVIEW":
            verdict = "Unclear / needs verification"
        elif action == "BLOCK":
            verdict = "Likely false / inconsistent"

        latency_ms = max(5, int((time.time() - start) * 1000))
        text_lc = (payload.get("text") or payload.get("claim") or "").strip().lower()

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,
            "score": int(score),
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
            "volatility_category": _volatility_category(text_lc),

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

        # DEMO returns ONE canonical shaped object
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
    # Local only (Render uses gunicorn)
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)

    
