# =============================
# TruCite Backend (Flask) - MVP+
# app.py (PART 1/4)
# =============================

import os
import re
import time
import json
import hashlib
from datetime import datetime, timezone
from urllib.parse import urlparse

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# -------------------------
# Versioning / defaults
# -------------------------
SCHEMA_VERSION = "2.0"
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
# =========================
# Demo Mode (VC signaling)
# =========================
DEMO_MODE = os.getenv("TRUCITE_DEMO_MODE", "0").strip() in ("1", "true", "TRUE", "yes", "YES")

# Stable, investor-friendly response contract version (separate from schema_version)
DEMO_CONTRACT_VERSION = "2026.02-demo"

# Deterministic request_id mode:
# - In demo mode, request_id is deterministic for the same (text + evidence + policy_mode)
# - In non-demo mode, request_id can be per-request random if you want later
DEMO_DETERMINISTIC_REQUEST_ID = True

# -------------------------
# Evidence validation constraints (MVP-safe)
# NOTE: This MVP does NOT fetch URLs server-side.
# It extracts URLs and assigns a deterministic trust tier.
# -------------------------
EVIDENCE_MAX_URLS = 2
EVIDENCE_TIMEOUT_SEC = 2.5   # reserved for future fetch (not used in MVP)
EVIDENCE_MAX_BYTES = 120_000 # reserved for future fetch (not used in MVP)

# -------------------------
# Trust tiers (deterministic / heuristic)
# A = primary authorities, official orgs, standards bodies
# B = reputable secondary sources
# C = unknown / user-provided / everything else
# -------------------------
TRUST_TIER_A_DOMAINS = {
    "apple.com",
    "nih.gov",
    "cdc.gov",
    "who.int",
    "cms.gov",
    "fda.gov",
    "sec.gov",
    "justice.gov",
    "europa.eu",
    "gov.uk",
}

TRUST_TIER_B_DOMAINS = {
    "wikipedia.org",
    "reuters.com",
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "nature.com",
    "sciencemag.org",
    "nejm.org",
    "jamanetwork.com",
    "theguardian.com",
    "nytimes.com",
    "wsj.com",
    "ft.com",
}

# -------------------------
# Policy profiles (simple knobs for MVP)
# -------------------------
POLICY_PROFILES = {
    "enterprise": {
        "volatile_trust_allowlist": ["A", "B"],
        "high_liability_requires_refs": True,
        "low_allow_score": 70,
        "low_review_score": 55,
        "high_allow_score": 80,
        "high_review_score": 60,
    },
    "health": {
        "volatile_trust_allowlist": ["A"],
        "high_liability_requires_refs": True,
        "low_allow_score": 75,
        "low_review_score": 60,
        "high_allow_score": 85,
        "high_review_score": 65,
    },
    "legal": {
        "volatile_trust_allowlist": ["A", "B"],
        "high_liability_requires_refs": True,
        "low_allow_score": 75,
        "low_review_score": 60,
        "high_allow_score": 85,
        "high_review_score": 65,
    },
    "finance": {
        "volatile_trust_allowlist": ["A", "B"],
        "high_liability_requires_refs": True,
        "low_allow_score": 75,
        "low_review_score": 60,
        "high_allow_score": 85,
        "high_review_score": 65,
    },
}

def policy_hash(policy_mode: str) -> str:
    pm = (policy_mode or DEFAULT_POLICY_MODE).strip().lower()
    base = f"{POLICY_VERSION}:{pm}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]

# -------------------------
# Text utilities
# -------------------------
def normalize_text(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())

def has_any_digit(s: str) -> bool:
    return bool(re.search(r"\d", (s or "")))

def contains_universal_certainty(s: str) -> bool:
    """
    Flags language like "always", "never", "guaranteed", etc.
    """
    tl = normalize_text(s)
    return bool(re.search(r"\b(always|never|guarantee(d)?|100%|cannot fail|no doubt|definitely)\b", tl))

# -------------------------
# URL extraction / evidence presence
# -------------------------
URL_REGEX = re.compile(r"(https?://[^\s)\]}>,\"']+)", re.IGNORECASE)

def extract_urls(evidence: str):
    ev = (evidence or "").strip()
    if not ev:
        return []
    urls = URL_REGEX.findall(ev)
    # Normalize / de-dupe preserving order
    seen = set()
    out = []
    for u in urls:
        u2 = u.strip().rstrip(".")
        if u2 and u2 not in seen:
            seen.add(u2)
            out.append(u2)
    return out[:EVIDENCE_MAX_URLS]

def evidence_present(evidence: str) -> bool:
    ev = (evidence or "").strip()
    if not ev:
        return False
    if extract_urls(ev):
        return True
    # Accept DOI / PMID style strings as "present" (not validated in MVP)
    if looks_like_doi(ev) or looks_like_pmid(ev):
        return True
    return False

def looks_like_doi(s: str) -> bool:
    # Very lightweight DOI pattern
    return bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", (s or ""), re.IGNORECASE))

def looks_like_pmid(s: str) -> bool:
    return bool(re.search(r"\bPMID[:\s]*\d{6,9}\b", (s or ""), re.IGNORECASE))

# -------------------------
# Domain trust tier
# -------------------------
def domain_root(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h

def domain_trust_tier(url: str) -> str:
    """
    Returns A | B | C
    """
    try:
        host = urlparse(url).netloc
        root = domain_root(host)
    except Exception:
        return "C"

    if not root:
        return "C"

    # Exact match or subdomain of known domains
    if root in TRUST_TIER_A_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_A_DOMAINS):
        return "A"
    if root in TRUST_TIER_B_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_B_DOMAINS):
        return "B"
    return "C"

def evidence_trust_summary(evidence: str):
    """
    Deterministic MVP summary of evidence quality.

    Returns:
      best_trust_tier: "A"|"B"|"C"|None
      evidence_status: "NONE"|"PRESENT"
      evidence_conf: float|None   (rough confidence)
    """
    ev = (evidence or "").strip()
    urls = extract_urls(ev)

    if not ev:
        return None, "NONE", None

    # DOI/PMID only
    if not urls and (looks_like_doi(ev) or looks_like_pmid(ev)):
        return "B", "PRESENT", 0.55

    if not urls:
        # Some text present but no URL/DOI/PMID
        return "C", "PRESENT", 0.25

    tiers = [domain_trust_tier(u) for u in urls]
    # Best tier (A > B > C)
    best = "C"
    if "A" in tiers:
        best = "A"
    elif "B" in tiers:
        best = "B"

    # Simple confidence mapping for MVP
    conf_map = {"A": 0.85, "B": 0.72, "C": 0.45}
    return best, "PRESENT", conf_map.get(best, 0.45)

def trust_allows_volatile(profile: dict, best_trust_tier: str) -> bool:
    """
    In enterprise/regulated modes, volatile facts require trusted evidence (A/B) to ALLOW.
    """
    if not best_trust_tier:
        return False
    allow = (profile or {}).get("volatile_trust_allowlist", ["A", "B"])
    return best_trust_tier in allow

# -------------------------
# Volatility + liability (MVP)
# -------------------------

VOLATILE_FACT_PATTERNS = [
    # current roles / titles / leadership positions
    r"\bcurrent\b",
    r"\bCEO\b",
    r"\bCFO\b",
    r"\bCOO\b",
    r"\bCTO\b",
    r"\bchairman\b",
    r"\bpresident\b",
    r"\bprime minister\b",
    r"\bgovernor\b",
    r"\bmayor\b",
    r"\bhead of\b",
    r"\bleadership\b",

    # phrasing often used in role assertions
    r"\bis the CEO of\b",
    r"\bis the (?:current )?(?:CEO|CFO|COO|CTO)\b",
    r"\bis (?:currently|now)\b",
]

EVENT_SENSITIVE_PATTERNS = [
    # time-sensitive events / news claims
    r"\btoday\b",
    r"\byesterday\b",
    r"\bthis week\b",
    r"\blast week\b",
    r"\bbreaking\b",
    r"\brecent\b",
    r"\bjust announced\b",
]

def volatility_level(text: str, policy_mode: str = "enterprise") -> str:
    """
    Returns: LOW | EVENT_SENSITIVE | VOLATILE
    VOLATILE = claims likely to change (current roles/titles, real-world status)
    EVENT_SENSITIVE = news-like time claims
    """
    tl = normalize_text(text or "")

    for pat in VOLATILE_FACT_PATTERNS:
        if re.search(pat, tl, re.I):
            return "VOLATILE"

    for pat in EVENT_SENSITIVE_PATTERNS:
        if re.search(pat, tl, re.I):
            return "EVENT_SENSITIVE"

    return "LOW"
    # -------------------------
# Liability tier classification
# -------------------------

HIGH_LIABILITY_KEYWORDS = [
    "dose",
    "dosage",
    "mg",
    "treatment",
    "diagnosis",
    "legal advice",
    "contract",
    "lawsuit",
    "financial advice",
    "investment",
    "interest rate",
    "prescription",
]

def liability_tier(text: str, policy_mode: str = "enterprise") -> str:
    """
    Returns: 'low' or 'high'
    Escalates for numeric claims or regulated domains.
    """
    tl = (text or "").strip().lower()

    # numeric claims are automatically high liability
    if any(ch.isdigit() for ch in tl):
        return "high"

    # keyword-based escalation
    for kw in HIGH_LIABILITY_KEYWORDS:
        if kw in tl:
            return "high"

    # stricter in regulated policy modes
    if policy_mode.lower() in ("health", "legal", "finance"):
        if any(word in tl for word in ["must", "always", "guarantee"]):
            return "high"

    return "low"
    # =============================
# Heuristic scoring + Decision Gate (MVP)
# app.py (PART 2/4)
# =============================

# -------------------------
# MVP heuristic scoring + guardrails
# Returns: score, verdict, explanation, signals, references
# -------------------------
def heuristic_score(text: str, evidence: str = "", policy_mode: str = DEFAULT_POLICY_MODE, seed_score: int = 55):
    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    # Evidence presence + trust summary (deterministic MVP)
    has_refs = evidence_present(ev)
    best_trust_tier, evidence_status, evidence_conf = evidence_trust_summary(ev)

    # Volatility + liability
    volatility = volatility_level(t, policy_mode=policy_mode)
    liability = liability_tier(t, policy_mode=policy_mode)

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
    if has_any_digit(t) and not has_refs:
        score -= 18
        risk_flags.append("numeric_without_evidence")
        rules_fired.append("numeric_without_evidence_penalty")

    # evidence present bonus
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # volatile guardrail (cap if no evidence)
    if volatility != "LOW" and not has_refs:
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

    signals = {
        "has_references": bool(has_refs),
        "reference_count": len(extract_urls(ev)),

        "liability_tier": liability,
        "volatility": volatility,
        "volatility_category": "",

        # Policy intent: evidence needed to ALLOW if volatile or high-liability
        "evidence_required_for_allow": bool(volatility != "LOW" or liability == "high"),

        # Evidence / trust
        "evidence_validation_status": evidence_status if has_refs else "NONE",
        "evidence_trust_tier": (best_trust_tier or ("B" if has_refs else "C")),
        "evidence_confidence": evidence_conf if has_refs else None,

        # Diagnostics
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail,
    }

    explanation = (
        "MVP heuristic scoring with volatility + liability gating. "
        "Replace with evidence-backed verification in production."
    )

    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})

    # DOI/PMID pass-through if present and no URLs
    if not references and (looks_like_doi(ev) or looks_like_pmid(ev)):
        references.append({"type": "evidence", "value": ev[:240]})

    return score, verdict, explanation, signals, references


# -------------------------
# Decision logic (policy-aware, volatility-aware, trust-aware)
# Returns: (action, reason)
# -------------------------
def decision_gate(score: int, signals: dict, policy_mode: str = DEFAULT_POLICY_MODE):
    pm = (policy_mode or DEFAULT_POLICY_MODE).strip().lower()
    profile = POLICY_PROFILES.get(pm, POLICY_PROFILES[DEFAULT_POLICY_MODE])

    guardrail = (signals.get("guardrail") or "").strip()
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    volatility = (signals.get("volatility") or "LOW").upper()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    best_trust = (signals.get("evidence_trust_tier") or None)
    trusted_for_volatile = trust_allows_volatile(profile, best_trust)

    # -------------------------
    # Hard guardrails
    # -------------------------
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # -------------------------
    # Volatile enforcement
    # -------------------------
    if volatility != "LOW":
        if not has_refs:
            return "REVIEW", "Volatile real-world fact detected (current roles/events). Evidence required to ALLOW."
        if not trusted_for_volatile:
            return "REVIEW", "Evidence provided but source trust tier insufficient for volatile ALLOW under policy."

    # -------------------------
    # High-liability enforcement (policy-driven)
    # -------------------------
    if profile.get("high_liability_requires_refs", True):
        if evidence_required_for_allow and not has_refs:
            if score >= 70:
                return "REVIEW", "Likely plausible, but evidence required under high-liability/volatile policy."
            return "REVIEW", "No evidence provided for high-liability/volatile claim. Human verification recommended."

    # -------------------------
    # Thresholds by liability tier
    # -------------------------
    if liability == "low":
        if score >= int(profile.get("low_allow_score", 70)):
            if volatility != "LOW" and has_refs and trusted_for_volatile:
                return "ALLOW", "Evidence present for volatile real-world fact. Approved under enterprise policy."
            return "ALLOW", "High confidence per MVP scoring."
        if score >= int(profile.get("low_review_score", 55)):
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    # high-liability tier
    if not has_refs:
        return "REVIEW", "High-liability content requires evidence to ALLOW. Human verification recommended."

    if score >= int(profile.get("high_allow_score", 80)):
        return "ALLOW", "High confidence with evidence under high-liability policy."
    if score >= int(profile.get("high_review_score", 60)):
        return "REVIEW", "Medium confidence. Human verification recommended."
    return "BLOCK", "Low confidence. Do not use without verification."
    # =============================
# Flask app + Routes
# app.py (PART 3/4)
# =============================

# -------------------------
# Flask app init
# -------------------------
app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)


# -------------------------
# Helpers: consistent JSON errors (so frontend never chokes)
# -------------------------
def json_error(code: str, message: str, status: int = 400, hint: str | None = None, extra: dict | None = None):
    payload = {
        "error_code": code,
        "message": message,
    }
    if hint:
        payload["hint"] = hint
    if extra and isinstance(extra, dict):
        payload.update(extra)
    return jsonify(payload), status
    # =========================
# Demo Mode helpers
# =========================
def make_request_id(text: str, evidence: str, policy_mode: str) -> str:
    """
    Demo-friendly request id.
    Deterministic in demo mode so the same input yields the same request_id
    (great for screenshots + reproducibility).
    """
    base = f"{(text or '').strip()}||{(evidence or '').strip()}||{(policy_mode or '').strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]


def shape_demo_response(resp_obj: dict) -> dict:
    """
    Produces a clean, investor-facing response while keeping detailed fields intact.
    This does NOT change your scoring. It only formats the payload.
    """
    # Always keep these (contract)
    shaped = {
        "contract": {
            "name": "TruCite Runtime Execution Reliability",
            "contract_version": DEMO_CONTRACT_VERSION,
            "schema_version": resp_obj.get("schema_version"),
        },

        # Outcome layer (what downstream systems enforce)
        "decision": resp_obj.get("decision"),
        "score": resp_obj.get("score"),
        "verdict": resp_obj.get("verdict"),

        # Policy metadata (why this decision occurred)
        "policy": {
            "mode": resp_obj.get("policy_mode"),
            "version": resp_obj.get("policy_version"),
            "hash": resp_obj.get("policy_hash"),
        },

        # Execution-bound audit artifact
        "audit": {
            "event_id": resp_obj.get("event_id"),
            "audit_fingerprint": resp_obj.get("audit_fingerprint"),
        },

        # Latency for “runtime gate” credibility
        "latency_ms": resp_obj.get("latency_ms"),

        # Evidence + references (show you’re model-agnostic + evidence-driven)
        "references": resp_obj.get("references", []),

        # Signals (keep: used by UI + demo transparency)
        "signals": resp_obj.get("signals", {}),

        # Human explanation (demo narrative)
        "explanation": resp_obj.get("explanation", ""),
    }

    return shaped
    


# -------------------------
# Landing page (Render-hosted frontend)
# - If you have /static/index.html this will serve it
# -------------------------
@app.route("/", methods=["GET"])
def index():
    try:
        return send_from_directory(app.static_folder, "index.html")
    except Exception:
        # If you don't have a frontend file yet, still respond OK.
        return (
            "TruCite backend is running. "
            "POST to /api/score with JSON {text, evidence?, policy_mode?}.",
            200,
        )


# -------------------------
# Health endpoint (JSON)
# -------------------------
@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "ok": True,
        "service": "trucite-backend",
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "default_policy_mode": DEFAULT_POLICY_MODE,
        "time_utc": datetime.now(timezone.utc).isoformat(),
    }), 200


# -------------------------
# Core scoring endpoint (JSON)
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

        # Scoring (always use our heuristic_score for stability)
        score, verdict, explanation, signals, references = heuristic_score(
            text=text,
            evidence=evidence,
            policy_mode=policy_mode,
        )

        # Decision gate
        action, reason = decision_gate(
            int(score),
            signals,
            policy_mode=policy_mode,
        )

         # =========================
        # DEMO MODE OVERRIDE
        # =========================
        if DEMO_MODE:
            # Force deterministic, investor-stable demo behavior
            if signals.get("volatility") == "VOLATILE" and not signals.get("has_references"):
                action = "REVIEW"
                reason = "Demo policy: volatile claim requires evidence."
                score = min(int(score), 65)
                verdict = "Unclear / needs verification"

            elif signals.get("has_references"):
                action = "ALLOW"
                reason = "Demo policy: evidence present."
                score = max(int(score), 78)
                verdict = "Likely true / consistent"       

        latency_ms = int((time.time() - start) * 1000)

        resp_obj = {
    "schema_version": SCHEMA_VERSION,
    "request_id": request_id,
    "latency_ms": latency_ms,

    # Outcome layer
    "verdict": verdict,
    "score": int(score),
    "decision": {
        "action": action,
        "reason": reason
    },

    # Policy metadata
    "policy_mode": policy_mode,
    "policy_version": POLICY_VERSION,
    "policy_hash": policy_hash(policy_mode),

    # Execution fingerprint
    "event_id": event_id,
    "audit_fingerprint": {
        "sha256": sha,
        "timestamp_utc": ts
    },

    # Diagnostic layer
    "claims": claims,
    "references": references,
    "signals": signals,

    # Human explanation
    "explanation": explanation,
        }
        # VC-signaling demo mode: return clean, contract-shaped payload
if DEMO_MODE:
    return jsonify(shape_demo_response(resp_obj)), 200

# Default: full payload
return jsonify(resp_obj), 200

    except Exception as e:
        # Return JSON error (so you can see WHAT broke on mobile)
        return json_error(
            "SERVER_EXCEPTION",
            str(e),
            500,
            hint="Likely indentation/paste error OR a missing helper above this section.",
        )
        # =============================
# app.py (PART 4/4)
# Static helpers + error handlers + local run
# =============================

# -------------------------
# Static asset passthrough (optional but helpful)
# -------------------------
@app.route("/static/<path:filename>", methods=["GET"])
def static_files(filename: str):
    return send_from_directory(app.static_folder, filename)


# -------------------------
# Optional: robots + favicon (won't break if missing)
# -------------------------
@app.route("/robots.txt", methods=["GET"])
def robots():
    return ("User-agent: *\nDisallow:\n", 200, {"Content-Type": "text/plain; charset=utf-8"})


@app.route("/favicon.ico", methods=["GET"])
def favicon():
    try:
        return send_from_directory(app.static_folder, "favicon.ico")
    except Exception:
        return ("", 204)


# -------------------------
# Error handlers: ALWAYS JSON for API paths
# -------------------------
@app.errorhandler(404)
def handle_404(err):
    # If browser hits unknown path, keep it simple
    if request.path.startswith("/api/") or request.path in ("/health",):
        return jsonify({
            "error_code": "NOT_FOUND",
            "message": f"Route not found: {request.path}",
        }), 404
    # For non-API, just show a small message
    return ("Not found", 404)


@app.errorhandler(405)
def handle_405(err):
    if request.path.startswith("/api/") or request.path in ("/health",):
        return jsonify({
            "error_code": "METHOD_NOT_ALLOWED",
            "message": f"Method not allowed for {request.path}",
        }), 405
    return ("Method not allowed", 405)


@app.errorhandler(500)
def handle_500(err):
    # Gunicorn/Flask will call this for unhandled exceptions
    if request.path.startswith("/api/") or request.path in ("/health",):
        return jsonify({
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "Internal server error",
        }), 500
    return ("Internal server error", 500)


# -------------------------
# Local dev run (Render uses gunicorn; safe to keep)
# -------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port, debug=True)
