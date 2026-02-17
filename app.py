import hashlib
import re
import time
from datetime import datetime, timezone
from flask import Flask, request, jsonify

app = Flask(__name__)

SCHEMA_VERSION = "2.0"
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
DEMO_CONTRACT_VERSION = "1.0"
DEMO_MODE = True
# -------------------------
# Helpers (safe + deterministic)
# -------------------------

def json_error(code: str, message: str, status: int = 500, hint: str | None = None):
    payload = {"error_code": code, "message": message}
    if hint:
        payload["hint"] = hint
    return jsonify(payload), status


def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def extract_urls(s: str):
    if not s:
        return []
    # simple, MVP-safe URL extraction
    return re.findall(r"https?://[^\s)>\"]+", s)


# -------------------------
# Evidence validation constraints (MVP-safe)
# -------------------------
EVIDENCE_MAX_URLS = 2
EVIDENCE_TIMEOUT_SEC = 2.5
EVIDENCE_MAX_BYTES = 120_000


def evidence_present(evidence: str) -> bool:
    ev = (evidence or "").strip()
    if not ev:
        return False
    return len(extract_urls(ev)) > 0


def domain_trust_tier(url: str) -> str:
    """
    Very lightweight trust heuristic for MVP:
      A = primary/official domains (apple.com, gov, edu, etc)
      B = common reputable org/news (basic allowlist)
      C = everything else
    """
    try:
        host = re.sub(r"^www\.", "", re.split(r"/", url.replace("https://", "").replace("http://", ""))[0].lower())
    except Exception:
        return "C"

    if host.endswith(".gov") or host.endswith(".edu") or host.endswith(".mil"):
        return "A"

    if host.endswith("apple.com"):
        return "A"

    # basic "B" examples (expand later)
    b_suffixes = (
        "who.int", "cdc.gov", "nih.gov", "nejm.org", "nature.com", "science.org",
        "reuters.com", "apnews.com", "bbc.co.uk", "nytimes.com", "wsj.com"
    )
    if any(host.endswith(x) for x in b_suffixes):
        return "B"

    return "C"


def evidence_trust_summary(evidence: str):
    """
    Returns: (best_trust_tier, evidence_validation_status, evidence_confidence)
    No crawling. Just presence + domain tier.
    """
    urls = extract_urls(evidence)[:EVIDENCE_MAX_URLS]
    if not urls:
        return None, "NONE", None

    tiers = [domain_trust_tier(u) for u in urls]
    best = "C"
    if "A" in tiers:
        best = "A"
    elif "B" in tiers:
        best = "B"

    # confidence is a demo heuristic (not a real probability)
    conf = 0.85 if best == "A" else 0.72 if best == "B" else 0.55
    return best, "PRESENT", conf


def has_any_digit(text: str) -> bool:
    return bool(re.search(r"\d", text or ""))


# -------------------------
# Volatility detection (MVP)
# -------------------------
VOLATILE_FACT_PATTERNS = [
    r"\bceo\b", r"\bcfo\b", r"\bcoo\b", r"\bcto\b",
    r"\bpresident\b", r"\bprime minister\b", r"\bmayor\b", r"\bgovernor\b",
    r"\bis the ceo of\b", r"\bis the (?:current )?(?:ceo|cfo|coo|cto)\b",
    r"\bcurrent\b", r"\bcurrently\b", r"\bnow\b"
]

EVENT_SENSITIVE_PATTERNS = [
    r"\btoday\b", r"\byesterday\b", r"\bthis week\b", r"\blast week\b",
    r"\bbreaking\b", r"\brecent\b", r"\bjust announced\b"
]


def volatility_level(text: str, policy_mode: str = DEFAULT_POLICY_MODE) -> str:
    tl = normalize_text(text or "")
    for pat in VOLATILE_FACT_PATTERNS:
        if re.search(pat, tl, re.I):
            return "VOLATILE"
    for pat in EVENT_SENSITIVE_PATTERNS:
        if re.search(pat, tl, re.I):
            return "EVENT_SENSITIVE"
    return "LOW"


# -------------------------
# Liability tier (MVP)
# -------------------------
HIGH_LIABILITY_KEYWORDS = [
    "diagnose", "treatment", "dose", "prescribe", "contraindication",
    "legal advice", "lawsuit", "contract", "liability",
    "investment", "buy", "sell", "financial advice", "tax"
]


def liability_tier(text: str, policy_mode: str = DEFAULT_POLICY_MODE) -> str:
    tl = normalize_text(text or "")
    pm = (policy_mode or DEFAULT_POLICY_MODE).strip().lower()

    if has_any_digit(text):
        return "high"

    if any(kw in tl for kw in HIGH_LIABILITY_KEYWORDS):
        return "high"

    # regulated modes are stricter
    if pm in ("health", "legal", "finance"):
        return "high" if len(tl) > 0 else "low"

    return "low"


def policy_hash(policy_mode: str) -> str:
    base = f"{POLICY_VERSION}:{(policy_mode or DEFAULT_POLICY_MODE).strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]
    # -------------------------
# MVP heuristic scoring + guardrails
# -------------------------

def heuristic_score(
    text: str,
    evidence: str = "",
    policy_mode: str = DEFAULT_POLICY_MODE,
    seed_score: int = 55,
):
    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

    liability = liability_tier(t, policy_mode=policy_mode)
    volatility = volatility_level(t, policy_mode=policy_mode)

    risk_flags = []
    rules_fired = []
    score = int(seed_score)
    guardrail = None

    # short declarative bonus
    if len(t) < 200 and " is " in tl:
        score += 18
        risk_flags.append("short_declarative_claim")
        rules_fired.append("short_declarative_bonus")

    # numeric without evidence
    if has_digit and not has_refs:
        score -= 18
        risk_flags.append("numeric_without_evidence")
        rules_fired.append("numeric_without_evidence_penalty")

    # evidence present bonus
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # volatile guardrail: cap without evidence
    if volatility in ("VOLATILE", "EVENT_SENSITIVE") and not has_refs:
        score = min(score, 65)
        guardrail = "volatile_current_fact_no_evidence"
        risk_flags.append("volatile_current_fact_no_evidence")
        rules_fired.append("volatile_current_fact_cap")

    score = max(0, min(100, int(score)))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    # Deterministic trust scoring (no crawling)
    best_trust_tier, evidence_status, evidence_conf = evidence_trust_summary(ev)

    evidence_required_for_allow = bool(volatility != "LOW" or liability == "high")

    signals = {
        "has_references": bool(has_refs),
        "reference_count": len(extract_urls(ev)),
        "liability_tier": liability,
        "volatility": volatility,
        "volatility_category": "",
        "evidence_required_for_allow": evidence_required_for_allow,
        "evidence_validation_status": evidence_status,
        "evidence_trust_tier": best_trust_tier or ("B" if has_refs else "C"),
        "evidence_confidence": evidence_conf,
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail,
    }

    explanation = (
        "MVP heuristic scoring with volatility + liability gating. "
        "Replace with evidence-backed verification in production."
    )

    references = [{"type": "url", "value": u} for u in extract_urls(ev)[:EVIDENCE_MAX_URLS]]

    return score, verdict, explanation, signals, references


# -------------------------
# Decision logic (policy-aware, volatility-aware, trust-aware)
# -------------------------

def decision_gate(score: int, signals: dict, policy_mode: str = DEFAULT_POLICY_MODE):
    has_refs = bool((signals or {}).get("has_references"))
    liability = ((signals or {}).get("liability_tier") or "low").lower()
    volatility = ((signals or {}).get("volatility") or "LOW").upper()
    evidence_required_for_allow = bool((signals or {}).get("evidence_required_for_allow"))

    # Hard guardrail
    guardrail = ((signals or {}).get("guardrail") or "").strip()
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Guardrail triggered."

    # Volatile facts: must have evidence to ALLOW
    if volatility != "LOW" and not has_refs:
        return "REVIEW", "Volatile real-world fact detected (current roles/events). Evidence required to ALLOW."

    # High-liability: evidence required to ALLOW
    if evidence_required_for_allow and not has_refs:
        return "REVIEW", "Evidence required under policy before ALLOW."

    # Thresholds
    if liability == "low":
        if score >= 75:
            return "ALLOW", "High confidence per MVP scoring."
        if score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    # High-liability tier
    if score >= 80 and has_refs:
        return "ALLOW", "High confidence with evidence under high-liability policy."
    if score >= 60:
        return "REVIEW", "Medium confidence. Human verification recommended."
    return "BLOCK", "Low confidence. Do not use without verification."
    # -------------------------
# API: /api/score
# -------------------------

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

        # Scoring
        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        # Decisioning
        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

        latency_ms = int((time.time() - start) * 1000)

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "decision": action,  # for legacy UI surfaces (string)
            "score": int(score),
            "verdict": verdict,
            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(policy_mode),
            "event_id": event_id,
            "audit_fingerprint_sha256": sha,
            "latency_ms": latency_ms,

            # UI fields
            "volatility": (signals.get("volatility") if isinstance(signals, dict) else "LOW"),
            "volatility_category": (signals.get("volatility_category", "") if isinstance(signals, dict) else ""),
            "evidence_validation_status": (signals.get("evidence_validation_status") if isinstance(signals, dict) else None),
            "evidence_trust_tier": (signals.get("evidence_trust_tier") if isinstance(signals, dict) else None),
            "evidence_confidence": (signals.get("evidence_confidence") if isinstance(signals, dict) else None),
            "risk_flags": (signals.get("risk_flags", []) if isinstance(signals, dict) else []),
            "guardrail": (signals.get("guardrail") if isinstance(signals, dict) else None),

            # Canonical decision object (what your frontend “Decision Gate” should use)
            "decision_detail": {"action": action, "reason": reason},

            # Debug panel payload
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "claims": [{"text": text}],
            "references": references,
            "signals": signals,
            "explanation": explanation,
        }

        return jsonify(resp_obj), 200

    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR a missing helper above this section.",
        )
        # -------------------------
# API: /verify  (alias of /api/score for the demo UI)
# -------------------------

@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
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

        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

        latency_ms = int((time.time() - start) * 1000)

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "decision": action,
            "score": int(score),
            "verdict": verdict,
            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(policy_mode),
            "event_id": event_id,
            "audit_fingerprint_sha256": sha,
            "latency_ms": latency_ms,

            "volatility": (signals.get("volatility") if isinstance(signals, dict) else "LOW"),
            "volatility_category": (signals.get("volatility_category", "") if isinstance(signals, dict) else ""),
            "evidence_validation_status": (signals.get("evidence_validation_status") if isinstance(signals, dict) else None),
            "evidence_trust_tier": (signals.get("evidence_trust_tier") if isinstance(signals, dict) else None),
            "evidence_confidence": (signals.get("evidence_confidence") if isinstance(signals, dict) else None),
            "risk_flags": (signals.get("risk_flags", []) if isinstance(signals, dict) else []),
            "guardrail": (signals.get("guardrail") if isinstance(signals, dict) else None),

            "decision_detail": {"action": action, "reason": reason},

            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "claims": [{"text": text}],
            "references": references,
            "signals": signals,
            "explanation": explanation,
        }

        return jsonify(resp_obj), 200

    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR a missing helper above this section.",
        )
        # -------------------------
# Health / sanity endpoints
# -------------------------

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "service": "trucite-backend",
        "policy_version": POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
    }), 200


@app.route("/", methods=["GET"])
def root():
    # Lightweight confirmation that the backend is reachable.
    return jsonify({
        "name": "TruCite Backend",
        "status": "ok",
        "endpoints": ["/api/score", "/verify", "/health"],
        "policy_version": POLICY_VERSION,
        "schema_version": SCHEMA_VERSION,
    }), 200
    # -------------------------
# Core API: /api/score
# -------------------------

@app.route("/api/score", methods=["POST", "OPTIONS"])
def api_score():
    # Always respond with JSON (even on errors) so frontend doesn't choke
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

        # Scoring (use our heuristic_score for stability)
        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        # Decision gate (canonical)
        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

        latency_ms = int((time.time() - start) * 1000)

        # Canonical response: decision is ALWAYS an object
        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,
            "score": int(score),

            "decision": {"action": action, "reason": reason},
            "decision_action": action,  # convenience for frontend

            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(policy_mode),

            "event_id": event_id,
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "audit_fingerprint_sha256": sha,  # convenience field your UI already reads

            # Convenience fields your UI panel reads
            "volatility": signals.get("volatility"),
            "volatility_category": signals.get("volatility_category", ""),
            "evidence_validation_status": signals.get("evidence_validation_status"),
            "evidence_trust_tier": signals.get("evidence_trust_tier"),
            "evidence_confidence": signals.get("evidence_confidence"),
            "risk_flags": signals.get("risk_flags", []),
            "guardrail": signals.get("guardrail"),

            # Debug payload
            "claims": claims,
            "references": references,
            "signals": signals,
            "explanation": explanation,
        }

        return jsonify(resp_obj), 200

    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR a missing helper above this section.",
        )
        # -------------------------
# UI API: /verify (mirrors /api/score)
# -------------------------

@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
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

        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

        latency_ms = int((time.time() - start) * 1000)

        resp_obj = {
            "schema_version": SCHEMA_VERSION,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,
            "score": int(score),

            "decision": {"action": action, "reason": reason},
            "decision_action": action,

            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(policy_mode),

            "event_id": event_id,
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "audit_fingerprint_sha256": sha,

            "volatility": signals.get("volatility"),
            "volatility_category": signals.get("volatility_category", ""),
            "evidence_validation_status": signals.get("evidence_validation_status"),
            "evidence_trust_tier": signals.get("evidence_trust_tier"),
            "evidence_confidence": signals.get("evidence_confidence"),
            "risk_flags": signals.get("risk_flags", []),
            "guardrail": signals.get("guardrail"),

            "claims": claims,
            "references": references,
            "signals": signals,
            "explanation": explanation,
        }

        return jsonify(resp_obj), 200

    except Exception as e:
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR a missing helper above this section.",
        )
        # -------------------------
# Local dev runner (Render uses gunicorn; this is for local only)
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
