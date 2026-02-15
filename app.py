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
            action, reason = globals()["decision_gate"](int(score), signals)
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

            "event_id": event_id,
            "policy_mode": policy_mode,
            "policy_version": policy_version,
            "policy_hash": ph,

            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},

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

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

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

    signals = {
        "liability_tier": tier,
        "volatility": volatility,
        "evidence_required_for_allow": (tier == "high" or volatility == "VOLATILE"),
        "has_digit": has_digit,
        "has_references": has_refs,
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail
    }

    explanation = (
        "MVP heuristic scoring with volatility + liability gating. "
        "Replace with evidence-backed verification in production."
    )

    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})

    return score, verdict, explanation, signals, references

# Decision logic (policy-aware, volatility-aware, trust-aware)
# -------------------------
def decision_gate(score: int, signals: dict, policy_mode: str = DEFAULT_POLICY_MODE):
    profile = POLICY_PROFILES.get(policy_mode, POLICY_PROFILES[DEFAULT_POLICY_MODE])

    guardrail = (signals.get("guardrail") or "").strip()
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    volatility = (signals.get("volatility") or "LOW").upper()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))
    evidence_signals = signals.get("evidence_signals") or {}

    best_trust = evidence_signals.get("best_trust_tier")
    trusted_for_volatile = trust_allows_volatile(profile, evidence_signals)

    # -------------------------
    # Hard guardrails
    # -------------------------
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # Volatile real-world fact requires trusted evidence to ALLOW
    if volatility != "LOW":
        if not has_refs:
            return "REVIEW", "Volatile real-world fact detected. Evidence required to ALLOW."
        if not trusted_for_volatile:
            return "REVIEW", "Evidence provided but source trust tier insufficient for volatile ALLOW under enterprise policy."

    # -------------------------
    # High-liability enforcement
    # -------------------------
    if evidence_required_for_allow and not has_refs:
        if score >= 70:
            return "REVIEW", "Likely plausible, but evidence required under high-liability policy."
        return "REVIEW", "No evidence provided for high-liability claim. Human verification recommended."

    # -------------------------
    # Liability-tier thresholds
    # -------------------------
    if liability == "low":
        if score >= 75:
            return "ALLOW", "High confidence under enterprise policy."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    # High-liability tier
    if score >= 80:
        return "ALLOW", "High confidence with trusted evidence under high-liability policy."
    elif score >= 60:
        return "REVIEW", "Medium confidence. Human verification recommended."
    return "BLOCK", "Low confidence. Do not use without verification."
    # -------------------------
# Core verification routine (shared by /verify and /api/score)
# -------------------------
def run_verification(payload: dict):
    start = time.time()

    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()
    policy_mode = (payload.get("policy_mode") or DEFAULT_POLICY_MODE).strip().lower()

    if not text:
        return None, ("Missing 'text' in request body", 400)

    # Fingerprint / Event ID
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    # Claims extraction
    claims = []
    if extract_claims:
        try:
            extracted = extract_claims(text)
            if isinstance(extracted, list):
                for c in extracted:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c["text"])})
                    elif isinstance(c, str):
                        claims.append({"text": c})
            elif isinstance(extracted, str):
                claims = [{"text": extracted}]
        except Exception:
            claims = [{"text": text}]
    else:
        claims = [{"text": text}]

    # Scoring
    if score_claim_text:
        try:
            out = score_claim_text(text, evidence=evidence, policy_mode=policy_mode)
            if isinstance(out, (list, tuple)) and len(out) >= 5:
                score, verdict, explanation, signals, references = out[:5]
            elif isinstance(out, (list, tuple)) and len(out) == 3:
                score, verdict, explanation = out
                score, verdict, explanation, signals, references = enrich_with_guardrails(
                    text, evidence, int(score), verdict, explanation
                )
            else:
                score, verdict, explanation, signals, references = heuristic_score(
                    text, evidence, policy_mode=policy_mode
                )
        except TypeError:
            # reference_engine may not accept evidence/policy_mode
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = enrich_with_guardrails(
                    text, evidence, int(score), verdict, explanation
                )
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(
                    text, evidence, policy_mode=policy_mode
                )
        except Exception:
            score, verdict, explanation, signals, references = heuristic_score(
                text, evidence, policy_mode=policy_mode
            )
    else:
        score, verdict, explanation, signals, references = heuristic_score(
            text, evidence, policy_mode=policy_mode
        )

    # Decision gate (updated signature)
    action, reason = decision_gate(int(score), signals, policy_mode=policy_mode)

    latency_ms = int((time.time() - start) * 1000)

    resp_obj = {
        "schema_version": SCHEMA_VERSION,
        "latency_ms": latency_ms,

        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},

        "event_id": event_id,
        "policy_mode": policy_mode,
        "policy_version": POLICY_VERSION,
        "policy_hash": policy_hash(policy_mode),

        "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},

        "claims": claims,
        "references": references,
        "signals": signals,
        "explanation": explanation,
    }

    return resp_obj, None
    
