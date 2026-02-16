import os
import time
import hashlib
import re
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS

# Optional modules (if present in repo)
try:
    from claim_parser import extract_claims
except Exception:
    extract_claims = None

try:
    from reference_engine import score_claim_text
except Exception:
    score_claim_text = None


app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

from flask import send_from_directory, make_response

# -------------------------
# Static landing page
# -------------------------
@app.get("/")
def landing():
    return send_from_directory(app.static_folder, "index.html")

# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "trucite-backend", "ts": int(time.time())})

# -------------------------
# OpenAPI stub (API credibility signal)
# -------------------------
@app.get("/openapi.json")
def openapi_spec():
    spec = {
        "openapi": "3.0.0",
        "info": {
            "title": "TruCite Verification API",
            "version": "1.0.0",
            "description": "Independent AI output verification and decision gating layer."
        },
        "paths": {
            "/api/score": {
                "post": {
                    "summary": "Audit-grade scoring endpoint",
                    "description": "Returns reliability score + enforceable ALLOW/REVIEW/BLOCK decision + audit fingerprint.",
                    "responses": {"200": {"description": "Scoring result"}}
                }
            },
            "/health": {
                "get": {"summary": "Service health check", "responses": {"200": {"description": "OK"}}}
            }
        }
    }
    return jsonify(spec)

# -------------------------
# /api/score (audit-grade scoring endpoint)
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
        policy_mode = (payload.get("policy_mode") or "enterprise").strip()  # safe default

        if not text:
            return jsonify({
                "error_code": "MISSING_TEXT",
                "message": "Missing 'text' in request body"
            }), 400

        # Safe fallbacks if globals are missing
        policy_version = globals().get("POLICY_VERSION", "2026.01")
        schema_version = globals().get("SCHEMA_VERSION", "2.0")

        # policy_hash fallback if function missing
        if "policy_hash" in globals() and callable(globals()["policy_hash"]):
            ph = globals()["policy_hash"](policy_mode)
        else:
            base = f"{policy_version}:{policy_mode.strip().lower()}"
            ph = hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]

        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        event_id = sha[:12]
        ts = datetime.now(timezone.utc).isoformat()

        # Use your existing heuristic_score if present; otherwise minimal safe scoring
        if "heuristic_score" in globals() and callable(globals()["heuristic_score"]):
            score, verdict, explanation, signals, references = globals()["heuristic_score"](text, evidence)
        else:
            score = 55
            verdict = "Unclear / needs verification"
            explanation = "Fallback scoring (heuristic_score not found)."
            signals = {"has_references": bool(evidence.strip()), "risk_flags": ["fallback_scoring"]}
            references = [{"type": "evidence", "value": evidence[:240]}] if evidence.strip() else []

        # Ensure volatility exists for UI
        if isinstance(signals, dict):
            if "volatility" not in signals:
                guardrail = (signals.get("guardrail") or "").strip().lower()
                signals["volatility"] = "VOLATILE" if "volatile" in guardrail else "LOW"
        else:
            signals = {"volatility": "LOW"}
         # Use your decision_gate if present; else safe default
    if "decision_gate" in globals() and callable(globals()["decision_gate"]):
        action, reason = globals()["decision_gate"](
            int(score),
            signals,
            policy_mode
        )
    else:
        action = "REVIEW"
        reason = "Fallback decisioning (decision_gate not found)."
        
        latency_ms = int((time.time() - start) * 1000)
resp_obj = {
            "schema_version": schema_version,
            "request_id": event_id,
            "latency_ms": latency_ms,

            "verdict": verdict,
            "score": int(score),

            "decision": {"action": action, "reason": reason},

            "policy_mode": policy_mode,
            "policy_version": policy_version,
            "policy_hash": ph,

            "event_id": event_id,
            "audit_fingerprint_sha256": sha,

            "volatility": signals.get("volatility"),
            "volatility_category": signals.get("volatility_category", ""),
            "evidence_validation_status": signals.get("evidence_validation_status"),
            "evidence_trust_tier": signals.get("evidence_trust_tier"),
            "evidence_confidence": signals.get("evidence_confidence"),

            "risk_flags": signals.get("risk_flags", []),
            "guardrail": signals.get("guardrail"),

            # keep detailed section below for debugging panel
            "claims": [{"text": text}],
            "references": references,
            "signals": signals,
            "explanation": explanation,
}

        return jsonify(resp_obj), 200

    except Exception as e:
        # Return JSON error (so you can see WHAT broke on mobile)
        return jsonify({
            "error_code": "SERVER_EXCEPTION",
            "message": str(e),
            "hint": "Likely missing symbol (POLICY_VERSION / SCHEMA_VERSION / heuristic_score / decision_gate) or indentation paste error."
        }), 500

# -------------------------
# Config (Phase 1.1 polish)
# -------------------------
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
SCHEMA_VERSION = "2.0"  # Level-2 JSON (adds latency_ms + schema_version)

# Evidence validation constraints (MVP-safe)
EVIDENCE_MAX_URLS = 2
EVIDENCE_TIMEOUT_SEC = 2.5
EVIDENCE_MAX_BYTES = 120_000

# -------------------------
# Evidence Trust (Deterministic, No Network Calls)
# -------------------------
from urllib.parse import urlparse

# Tier A = High-authority primary sources
TRUST_TIER_A_DOMAINS = {
    "nih.gov", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "cdc.gov", "who.int", "fda.gov", "cms.gov",
    "nature.com", "science.org", "nejm.org", "thelancet.com",
    "jamanetwork.com", "bmj.com",
    "ieee.org", "acm.org", "iso.org", "nist.gov",
}

# Tier B = Official company / institutional sources
TRUST_TIER_B_DOMAINS = {
    "apple.com", "microsoft.com", "openai.com",
    "google.com", "amazon.com",
    "sec.gov", "ftc.gov",
    "reuters.com", "apnews.com", "bloomberg.com",
    "wsj.com", "ft.com",
}

def _domain_root(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h

def domain_trust_tier(url: str) -> str:
    """
    Returns: "A" | "B" | "C"
    Deterministic domain-only trust scoring.
    """
    try:
        host = urlparse(url).netloc
        root = _domain_root(host)
    except Exception:
        return "C"

    if not root:
        return "C"

    # gov/edu rule
    if root.endswith(".gov") or root.endswith(".edu"):
        return "A"

    if root in TRUST_TIER_A_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_A_DOMAINS):
        return "A"

    if root in TRUST_TIER_B_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_B_DOMAINS):
        return "B"

    return "C"

def evidence_trust_summary(evidence: str):
    """
    Returns:
      best_trust_tier
      evidence_status
      confidence_score
    """
    ev = (evidence or "").strip()

    if not ev:
        return None, "NONE", None

    urls = extract_urls(ev)[:EVIDENCE_MAX_URLS]

    if not urls:
        if looks_like_doi_or_pmid(ev):
            return "B", "IDENTIFIER_ONLY", 0.65
        return "C", "PRESENT", 0.40

    tiers = [domain_trust_tier(u) for u in urls]

    if "A" in tiers:
        return "A", "PRESENT", 0.90

    if "B" in tiers:
        return "B", "PRESENT", 0.72

    return "C", "PRESENT", 0.50

# Basic API hardening signals (acquirer-friendly)
DEFAULT_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def with_headers(resp):
    for k, v in DEFAULT_HEADERS.items():
        resp.headers[k] = v
    return resp


def policy_hash(policy_mode: str) -> str:
    base = f"{POLICY_VERSION}:{(policy_mode or DEFAULT_POLICY_MODE).strip().lower()}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:12]


# -------------------------
# Policy profiles (VC signaling: control-plane)
# -------------------------
POLICY_PROFILES = {
    "enterprise": {
        "allow_threshold_low": 70,
        "review_threshold": 55,
        "allow_threshold_high": 75,
        "require_evidence_for_high_liability": True,
        "require_evidence_for_volatile_allow": True,
        "volatile_trust_allowlist": ["A", "B"],  # VOLATILE requires Tier A/B to ALLOW
    },
    "health": {
        "allow_threshold_low": 72,
        "review_threshold": 58,
        "allow_threshold_high": 80,
        "require_evidence_for_high_liability": True,
        "require_evidence_for_volatile_allow": True,
        "volatile_trust_allowlist": ["A", "B"],
    },
    "legal": {
        "allow_threshold_low": 72,
        "review_threshold": 58,
        "allow_threshold_high": 80,
        "require_evidence_for_high_liability": True,
        "require_evidence_for_volatile_allow": True,
        "volatile_trust_allowlist": ["A", "B"],
    },
    "finance": {
        "allow_threshold_low": 73,
        "review_threshold": 60,
        "allow_threshold_high": 82,
        "require_evidence_for_high_liability": True,
        "require_evidence_for_volatile_allow": True,
        "volatile_trust_allowlist": ["A", "B"],
    },
}


def get_profile(policy_mode: str) -> dict:
    pm = (policy_mode or DEFAULT_POLICY_MODE).strip().lower()
    return POLICY_PROFILES.get(pm, POLICY_PROFILES[DEFAULT_POLICY_MODE])

# -------------------------
# Guardrails + parsing helpers
# -------------------------

UNIVERSAL_CERTAINTY_TERMS = [
    "always", "never", "guaranteed", "definitely", "proves", "proof", "100%", "cures", "cure", "no doubt"
]

KNOWN_FALSE_PATTERNS = [
    r"\bthe\s+earth\s+is\s+flat\b",
    r"\bearth\s+is\s+flat\b",
    r"\bflat\s+earth\b",
    r"\bvaccines?\s+cause\s+autism\b",
    r"\b5g\s+causes?\s+covid\b",
    r"\bmoon\s+landing\s+was\s+fake\b",
]

HIGH_LIABILITY_KEYWORDS = [
    "dose", "dosage", "mg", "mcg", "units", "diagnosis", "treat", "treatment",
    "contraindication", "side effect", "guideline", "clinical", "patient",
    "prescribe", "medication", "drug", "insulin", "warfarin",
    "contract", "liability", "lawsuit", "indemnify", "breach",
    "statute", "jurisdiction", "legal advice",
    "roi", "interest rate", "apr", "yield", "stock",
    "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs"
]

VOLATILE_FACT_PATTERNS = [
    r"\bprime\s+minister\b",
    r"\bpresident\b",
    r"\bchancellor\b",
    r"\bgovernor\b",
    r"\bmayor\b",
    r"\bceo\b",
    r"\bcfo\b",
    r"\bchief\s+medical\s+officer\b",
    r"\bcurrent\b",
    r"\bas\s+of\s+\d{4}\b",
    r"\btoday\b",
    r"\bright\s+now\b",
    r"\bis\s+the\s+(ceo|president|prime\s+minister|governor|mayor)\b",
]

def normalize_text(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^\w\s%./:-]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s

def has_any_digit(s: str) -> bool:
    return any(ch.isdigit() for ch in (s or ""))

def extract_urls(s: str):
    if not s:
        return []
    return re.findall(r"https?://[^\s)]+", s)

def evidence_present(evidence: str) -> bool:
    if not evidence:
        return False
    if extract_urls(evidence):
        return True
    return len(evidence.strip()) >= 12

def matches_known_false(text: str) -> bool:
    tl = normalize_text(text)
    for pat in KNOWN_FALSE_PATTERNS:
        if re.search(pat, tl, re.I):
            return True
    return False

def matches_volatile_current_fact(text: str) -> bool:
    tl = normalize_text(text)
    for pat in VOLATILE_FACT_PATTERNS:
        if re.search(pat, tl, re.I):
            return True
    return False

def volatility_level(text: str, policy_mode: str = "enterprise") -> str:
    if matches_volatile_current_fact(text):
        return "VOLATILE"
    return "LOW"

def liability_tier(text: str, policy_mode: str = "enterprise") -> str:
    tl = normalize_text(text)
    if has_any_digit(text):
        return "high"
    for kw in HIGH_LIABILITY_KEYWORDS:
        if kw in tl:
            return "high"
    return "low"
# -------------------------
# MVP heuristic scoring + guardrails
# -------------------------
def heuristic_score(text: str, evidence: str = "", policy_mode: str = "enterprise", seed_score: int = 55):
    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    # Evidence presence
    has_refs = evidence_present(ev)
    urls = extract_urls(ev)
    has_digit = has_any_digit(t)

    # Policy-aware signals
    tier = liability_tier(t, policy_mode=policy_mode)
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

    # evidence bonus
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # volatile guardrail
    if volatility == "VOLATILE" and not has_refs:
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

    # Deterministic trust scoring (safe even if you’re not fetching)
    best_trust_tier, evidence_status, evidence_conf = evidence_trust_summary(ev)

    signals = {
        "has_references": bool(has_refs),
        "reference_count": len(urls),

        "liability_tier": tier,
        "volatility": volatility,

        "evidence_required_for_allow": bool(volatility != "LOW" or tier == "high"),

        "evidence_validation_status": evidence_status,  # e.g., "PRESENT" / "NONE"
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

    references = [{"type": "url", "value": u} for u in urls]

    return score, verdict, explanation, signals, references
# -------------------------
# Decision logic (canonical)
# -------------------------
def decision_gate(score: int, signals: dict, policy_mode: str = "enterprise"):
    """
    Canonical decision gate for MVP.
    Keeps behavior deterministic and explainable.

    Inputs:
      - score: 0-100 heuristic score
      - signals: dict from heuristic_score / enrich_with_guardrails
      - policy_mode: enterprise | health | legal | finance
    """
    pm = (policy_mode or "enterprise").strip().lower()

    guardrail = (signals.get("guardrail") or "").strip()
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    volatility = (signals.get("volatility") or "LOW").upper()

    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    # Evidence trust summary (optional in MVP; safe defaults)
    trust_tier = (signals.get("evidence_trust_tier") or "C").upper()
    evidence_status = (signals.get("evidence_validation_status") or ("PRESENT" if has_refs else "NONE")).upper()

    # -------------------------
    # Hard guardrails (highest precedence)
    # -------------------------
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    if guardrail == "volatile_current_fact_no_evidence":
        return "REVIEW", "Volatile real-world fact detected (current roles/events). Evidence required to ALLOW."

    # -------------------------
    # Volatility policy: require evidence for volatile facts
    # -------------------------
    if volatility != "LOW":
        if not has_refs:
            return "REVIEW", "Volatile real-world fact detected. Evidence required to ALLOW."

        # In enterprise + regulated modes, require at least B trust for volatile ALLOW
        if pm in ("enterprise", "health", "legal", "finance"):
            if trust_tier not in ("A", "B"):
                return "REVIEW", "Evidence provided but source trust tier insufficient for volatile ALLOW under enterprise policy."

    # -------------------------
    # High-liability enforcement: require evidence to ALLOW
    # -------------------------
    if evidence_required_for_allow and not has_refs:
        if score >= 70:
            return "REVIEW", "Likely plausible, but evidence required under high-liability/volatile policy."
        return "REVIEW", "No evidence provided for high-liability/volatile claim. Human verification recommended."

    # -------------------------
    # Thresholds (liability-aware)
    # -------------------------
    if liability == "low":
        if score >= 70:
            # Special messaging for volatile + evidence (your Cook demo)
            if volatility != "LOW" and has_refs and trust_tier in ("A", "B") and evidence_status in ("PRESENT", "FETCH_OK", "OK"):
                return "ALLOW", "Evidence present for volatile real-world fact. Approved under enterprise policy."
            return "ALLOW", "High confidence per MVP scoring."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    # High-liability tier
    if score >= 80:
        return "ALLOW", "High confidence with evidence under high-liability policy."
    elif score >= 60:
        return "REVIEW", "Medium confidence. Human verification recommended."
    return "BLOCK", "Low confidence. Do not use without verification."
