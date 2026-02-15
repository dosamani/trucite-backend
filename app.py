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
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()
    policy_mode = (payload.get("policy_mode") or DEFAULT_POLICY_MODE).strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body"}), 400

    # Fingerprint / Event ID
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    # Minimal claims (keeps demo stable even if optional modules missing)
    claims = [{"text": text}]

    # Heuristic scoring (uses your existing heuristic_score)
    score, verdict, explanation, signals, references = heuristic_score(text, evidence)

    # Volatility label for UI (simple mapping from guardrail)
    volatility = "VOLATILE" if signals.get("guardrail") == "volatile_current_fact_no_evidence" else "LOW"
    signals["volatility"] = volatility

    action, reason = decision_gate(int(score), signals)

    resp_obj = {
        "schema_version": SCHEMA_VERSION,
        "request_id": event_id,
        "latency_ms": 0,

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

    return jsonify(resp_obj), 200

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
    # medical
    "dose", "dosage", "mg", "mcg", "units", "diagnosis", "treat", "treatment", "contraindication", "side effect",
    "guideline", "clinical", "patient", "prescribe", "medication", "drug", "insulin", "warfarin",
    # legal
    "contract", "liability", "lawsuit", "indemnify", "breach", "statute", "jurisdiction", "legal advice",
    "precedent", "case law", "plaintiff", "defendant",
    # finance
    "roi", "interest rate", "apr", "yield", "stock", "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs", "sec"
]

# Volatility taxonomy
# - VOLATILE: current roles/titles, time-sensitive facts, "today", "right now", "current"
VOLATILE_FACT_PATTERNS = [
    r"\bprime\s+minister\b",
    r"\bpresident\b",
    r"\bchancellor\b",
    r"\bgovernor\b",
    r"\bmayor\b",
    r"\bceo\b",
    r"\bcfo\b",
    r"\bcurrent\b",
    r"\btoday\b",
    r"\bright\s+now\b",
    r"\bas\s+of\s+\d{4}\b",
    r"\bis\s+the\s+(ceo|president|prime\s+minister|governor|mayor)\b",
    r"\bwho\s+is\s+the\s+(ceo|president|prime\s+minister|governor|mayor)\b",
]

# - EVENT_SENSITIVE: elections, wars, disasters, incidents, breaking-news style facts (still volatile-ish)
EVENT_SENSITIVE_PATTERNS = [
    r"\belection\b",
    r"\bwon\b",
    r"\bresults\b",
    r"\bbreaking\b",
    r"\bearthquake\b",
    r"\bshooting\b",
    r"\battack\b",
    r"\bwar\b",
    r"\bceasefire\b",
    r"\bmerger\b",
    r"\bacquired\b",
    r"\bipo\b",
]

# Trust tier allowlist (very lightweight; can be expanded later)
TRUST_TIER_A_DOMAINS = [
    "apple.com",
    "cdc.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "who.int",
    "sec.gov",
    "irs.gov",
    "europa.eu",
    "justice.gov",
]
TRUST_TIER_B_DOMAINS = [
    "reuters.com",
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "ft.com",
    "wsj.com",
    "nytimes.com",
    "theguardian.com",
    "nature.com",
    "science.org",
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
    urls = re.findall(r"https?://[^\s)]+", s)
    # de-dupe while preserving order
    seen = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out

def looks_like_doi_or_pmid(s: str) -> bool:
    if not s:
        return False
    s = s.strip()
    if re.search(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", s, re.I):
        return True
    if re.search(r"\bPMID:\s*\d+\b", s, re.I):
        return True
    if re.search(r"\bpubmed\.ncbi\.nlm\.nih\.gov/\d+\b", s, re.I):
        return True
    return False

def evidence_present(evidence: str) -> bool:
    if not evidence:
        return False
    if extract_urls(evidence):
        return True
    if looks_like_doi_or_pmid(evidence):
        return True
    return len(evidence.strip()) >= 12

def is_short_declarative(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 160:
        return False
    tl = normalize_text(t)
    return (" is " in tl) or (" are " in tl) or t.endswith(".")

def contains_universal_certainty(text: str) -> bool:
    tl = normalize_text(text)
    return any(w in tl for w in UNIVERSAL_CERTAINTY_TERMS)

def matches_known_false(text: str) -> bool:
    tl = normalize_text(text)
    for pat in KNOWN_FALSE_PATTERNS:
        if re.search(pat, tl, re.I):
            return True
    return False

def detect_volatility(text: str) -> str:
    """
    Returns: LOW | VOLATILE | EVENT_SENSITIVE
    """
    tl = normalize_text(text)
    for pat in VOLATILE_FACT_PATTERNS:
        if re.search(pat, tl, re.I):
            return "VOLATILE"
    for pat in EVENT_SENSITIVE_PATTERNS:
        if re.search(pat, tl, re.I):
            return "EVENT_SENSITIVE"
    return "LOW"

def liability_tier(text: str, policy_mode: str) -> str:
    """
    policy_mode-aware liability escalation:
    - health/legal/finance are stricter: more things count as "high"
    """
    tl = normalize_text(text)
    pm = (policy_mode or DEFAULT_POLICY_MODE).strip().lower()

    if has_any_digit(text):
        return "high"

    # base: keyword driven
    for kw in HIGH_LIABILITY_KEYWORDS:
        if kw in tl:
            return "high"

    # mode-specific escalation
    if pm in ("health", "legal", "finance"):
        # any strong certainty claims in these modes are treated as high-liability without evidence
        if contains_universal_certainty(text):
            return "high"

    return "low"

def domain_root(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("www."):
        h = h[4:]
    return h

def domain_trust_tier(url: str) -> str:
    """
    A | B | C
    """
    try:
        host = urlparse(url).netloc
        root = domain_root(host)
    except Exception:
        return "C"

    if not root:
        return "C"

    if root in TRUST_TIER_A_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_A_DOMAINS):
        return "A"
    if root in TRUST_TIER_B_DOMAINS or any(root.endswith("." + d) for d in TRUST_TIER_B_DOMAINS):
        return "B"
    return "C"
# -------------------------
# Evidence validation (MVP-safe, not a crawler)
# -------------------------
def safe_head_or_get(url: str):
    """
    Returns: (ok: bool, status_code: int|None, content_type: str|None, final_url: str, error: str|None)
    Minimal request to validate a URL exists and is reachable.
    """
    headers = {
        "User-Agent": "TruCiteEvidenceBot/0.1",
        "Accept": "text/html,application/pdf,application/json;q=0.9,*/*;q=0.8",
    }

    # Try HEAD first (some servers block; then fallback to GET with small read)
    for method in ("HEAD", "GET"):
        try:
            req = Request(url, headers=headers, method=method)
            with urlopen(req, timeout=EVIDENCE_TIMEOUT_SEC) as resp:
                status = getattr(resp, "status", None) or getattr(resp, "code", None)
                ctype = resp.headers.get("Content-Type", "")
                final_url = resp.geturl() if hasattr(resp, "geturl") else url

                if method == "GET":
                    # read a tiny amount to ensure the response is real
                    _ = resp.read(min(EVIDENCE_MAX_BYTES, 2048))

                return True, int(status) if status is not None else 200, (ctype or ""), final_url, None
        except HTTPError as e:
            return False, int(getattr(e, "code", 0) or 0), None, url, f"http_error:{getattr(e, 'code', '')}"
        except URLError:
            return False, None, None, url, "url_error"
        except Exception:
            return False, None, None, url, "unknown_error"

    return False, None, None, url, "unknown_error"


def validate_evidence(evidence: str):
    """
    Produces:
      - references: list[{type,url,value,trust_tier,status,content_type}]
      - evidence_signals: dict (counts + best_trust_tier + any_fetch_ok)
    """
    ev = (evidence or "").strip()
    urls = extract_urls(ev)[:EVIDENCE_MAX_URLS]

    references = []
    fetch_ok = False
    best_tier = None
    tiers_seen = []

    for u in urls:
        tier = domain_trust_tier(u)
        ok, status, ctype, final_url, err = safe_head_or_get(u)

        if ok:
            fetch_ok = True

        tiers_seen.append(tier)
        if best_tier is None:
            best_tier = tier
        else:
            # A is best, then B, then C
            if tier == "A":
                best_tier = "A"
            elif tier == "B" and best_tier != "A":
                best_tier = "B"

        references.append({
            "type": "url",
            "value": u,
            "final_url": final_url,
            "trust_tier": tier,
            "fetch_ok": bool(ok),
            "http_status": status,
            "content_type": (ctype or "")[:120],
            "error": err
        })

    # DOI/PMID evidence (non-fetchable in MVP; treat as present but unvalidated)
    if looks_like_doi_or_pmid(ev) and not urls:
        references.append({"type": "evidence", "value": ev[:240], "trust_tier": "B", "fetch_ok": False})

    evidence_signals = {
        "url_count": len(urls),
        "any_fetch_ok": bool(fetch_ok),
        "best_trust_tier": best_tier or ("B" if looks_like_doi_or_pmid(ev) else None),
        "tiers_seen": tiers_seen,
    }

    return references, evidence_signals


def trust_allows_volatile(profile: dict, evidence_signals: dict) -> bool:
    """
    In enterprise/regulated modes, volatile facts require trusted evidence (A/B) to ALLOW.
    """
    best = (evidence_signals or {}).get("best_trust_tier")
    if not best:
        return False
    allow = profile.get("volatile_trust_allowlist", ["A", "B"])
    return best in allow
    # -------------------------
# MVP heuristic scoring + guardrails (enhanced)
# -------------------------
def heuristic_score(text: str, evidence: str = "", policy_mode: str = DEFAULT_POLICY_MODE, seed_score: int = 55, **kwargs):
    """
    Enhanced heuristic scoring:
      - Integrates evidence fetch validation
      - Domain trust-tier shaping
      - Volatility classification
      - Policy-mode aware scoring
    """

    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    profile = POLICY_PROFILES.get(policy_mode, POLICY_PROFILES[DEFAULT_POLICY_MODE])

    # --- Evidence validation ---
    references, evidence_signals = validate_evidence(ev)
    has_refs = bool(evidence_signals.get("url_count") or looks_like_doi_or_pmid(ev))
    any_fetch_ok = evidence_signals.get("any_fetch_ok", False)
    best_trust = evidence_signals.get("best_trust_tier")

    # --- Core feature extraction ---
    has_digit = has_any_digit(t)
    tier = liability_tier(t)
    volatility = volatility_level(t)
    evidence_required_for_allow = (tier == "high")

    risky_terms = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely", "no doubt"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    risk_flags = []
    rules_fired = []
    score = int(seed_score)
    guardrail = None

    # -------------------------
    # Linguistic signals
    # -------------------------
    if any(w in tl for w in risky_terms):
        score -= 15
        risk_flags.append("high_certainty_language")
        rules_fired.append("high_certainty_language_penalty")

    if any(w in tl for w in hedges):
        score += 10
        risk_flags.append("hedging_language")
        rules_fired.append("hedging_language_bonus")

    if len(t) > 800:
        score -= 10
        risk_flags.append("very_long_output")
        rules_fired.append("very_long_output_penalty")

    # -------------------------
    # Numeric / liability shaping
    # -------------------------
    if has_digit and not has_refs:
        score -= 18
        risk_flags.append("numeric_without_evidence")
        rules_fired.append("numeric_without_evidence_penalty")

    if has_digit and has_refs:
        score += 8
        risk_flags.append("numeric_with_evidence")
        rules_fired.append("numeric_with_evidence_bonus")

    short_decl = is_short_declarative(t)
    if short_decl and not has_digit:
        risk_flags.append("short_declarative_claim")
        score += 18
        rules_fired.append("short_declarative_bonus")

    # -------------------------
    # Guardrails
    # -------------------------
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        rules_fired.append("known_false_category_cap")
        guardrail = "known_false_claim_no_evidence"

    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        rules_fired.append("unsupported_universal_claim_cap")
        guardrail = "unsupported_universal_claim_no_evidence"

    if volatility != "LOW" and not has_refs and guardrail is None:
        score = min(score, 65)
        risk_flags.append("volatile_current_fact_no_evidence")
        rules_fired.append("volatile_current_fact_cap")
        guardrail = "volatile_current_fact_no_evidence"

    # -------------------------
    # Evidence trust shaping
    # -------------------------
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

        if any_fetch_ok:
            score += 5
            risk_flags.append("evidence_fetch_ok")
            rules_fired.append("evidence_fetch_bonus")

        if best_trust == "A":
            score += 6
            risk_flags.append("tier_a_source")
            rules_fired.append("tier_a_bonus")

        elif best_trust == "B":
            score += 3
            risk_flags.append("tier_b_source")
            rules_fired.append("tier_b_bonus")

    # Conservative cap for high-liability without evidence
    if tier == "high" and not has_refs:
        score = min(score, 73)
        risk_flags.append("high_liability_without_evidence_cap")
        rules_fired.append("high_liability_without_evidence_cap")

    score = max(0, min(100, int(score)))

    # -------------------------
    # Verdict bands
    # -------------------------
    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "Enhanced heuristic score with volatility detection, domain trust scoring, "
        "live evidence validation, and enterprise policy shaping. "
        "Replace with deeper semantic verification in production."
    )

    signals = {
        "liability_tier": tier,
        "evidence_required_for_allow": bool(evidence_required_for_allow),
        "has_digit": bool(has_digit),
        "has_references": bool(has_refs),
        "reference_count": len(references),
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail,
        "volatility": volatility,
        "evidence_signals": evidence_signals
    }

    return score, verdict, explanation, signals, references
    # -------------------------
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
    
