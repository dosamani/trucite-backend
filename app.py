import os
import time
import hashlib
import re
from datetime import datetime, timezone

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

# -------------------------
# Config (Phase 1 polish)
# -------------------------
POLICY_VERSION = "2026.01"
DEFAULT_POLICY_MODE = "enterprise"
SCHEMA_VERSION = "2.0"  # Level-2 JSON (adds latency_ms + schema_version)

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
    # finance
    "roi", "interest rate", "apr", "yield", "stock", "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs"
]

# Volatile fact patterns (current roles/events/titles)
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

def matches_volatile_current_fact(text: str) -> bool:
    tl = normalize_text(text)
    for pat in VOLATILE_FACT_PATTERNS:
        if re.search(pat, tl, re.I):
            return True
    return False

def liability_tier(text: str) -> str:
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
def heuristic_score(text: str, evidence: str = "", seed_score: int = 55):
    """
    MVP heuristic scoring (0-100) + conservative guardrails.
    Adds:
      - Known-false guardrail
      - Unsupported universal certainty guardrail
      - Volatile current-fact guardrail (prevents stale ALLOWs without evidence)
    """

    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

    # References extraction (demo only)
    references = []
    for u in extract_urls(ev):
        references.append({"type": "url", "value": u})
    if looks_like_doi_or_pmid(ev) and not extract_urls(ev):
        references.append({"type": "evidence", "value": ev[:240]})

    has_refs = evidence_present(ev)
    has_digit = has_any_digit(t)

    tier = liability_tier(t)
    evidence_required_for_allow = (tier == "high")

    risky_terms = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely", "no doubt"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    risk_flags = []
    rules_fired = []
    score = int(seed_score)
    guardrail = None

    # certainty / hedging signals
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

    # Numeric / liability: penalize unless evidence
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

    # Guardrail #1: Known false categories
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        rules_fired.append("known_false_category_cap")
        guardrail = "known_false_claim_no_evidence"

    # Guardrail #2: Unsupported universal certainty w/out evidence
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        rules_fired.append("unsupported_universal_claim_cap")
        guardrail = "unsupported_universal_claim_no_evidence"

    # Guardrail #3: Volatile current facts (leaders, titles, etc.)
    if matches_volatile_current_fact(t) and not has_refs and guardrail is None:
        score = min(score, 65)  # prevents easy ALLOW
        risk_flags.append("volatile_current_fact_no_evidence")
        rules_fired.append("volatile_current_fact_cap")
        guardrail = "volatile_current_fact_no_evidence"

    # Evidence helps but does not guarantee ALLOW
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # Conservative cap for high-liability without evidence
    if tier == "high" and not has_refs:
        score = min(score, 73)
        risk_flags.append("high_liability_without_evidence_cap")
        rules_fired.append("high_liability_without_evidence_cap")

    score = max(0, min(100, int(score)))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty/uncertainty cues, risk signals, "
        "and applies conservative handling for numeric/high-liability content unless evidence is provided. "
        "It also includes lightweight guardrails for debunked categories, unsupported universal certainty, "
        "and volatile real-world facts (current roles/events) to reduce false ALLOW outcomes. "
        "Replace with evidence-backed verification in production."
    )

    signals = {
        "liability_tier": tier,
        "evidence_required_for_allow": bool(evidence_required_for_allow),
        "has_digit": bool(has_digit),
        "has_references": bool(has_refs),
        "reference_count": len(references),
        "risk_flags": risk_flags,
        "rules_fired": rules_fired,
        "guardrail": guardrail
    }

    return score, verdict, explanation, signals, references


def enrich_with_guardrails(text: str, evidence: str, score: int, verdict: str, explanation: str):
    base_score = int(max(0, min(100, score)))
    s, v, e, signals, references = heuristic_score(text, evidence, seed_score=base_score)
    if verdict and isinstance(verdict, str):
        v = verdict
    if explanation and isinstance(explanation, str):
        e = explanation
    return s, v, e, signals, references


# -------------------------
# Decision logic
# -------------------------
def decision_gate(score: int, signals: dict):
    """
    Demo policy (acquirer-friendly):
      - Known-debunked categories: BLOCK unless evidence
      - High-liability or numeric claims: evidence required to reach ALLOW
      - Volatile current-event facts: REVIEW unless evidence (prevents stale ALLOWs)
      - Low-liability non-numeric: can ALLOW at a lower threshold (>=70)
    """
    guardrail = (signals.get("guardrail") or "").strip()
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    # Hard guardrails first
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Guardrail triggered."

    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    if guardrail == "volatile_current_fact_no_evidence":
        return "REVIEW", "Volatile real-world fact detected (current roles/events). Evidence required to ALLOW."

    # Enforce evidence for ALLOW in high-liability (or numeric) cases
    if evidence_required_for_allow and not has_refs:
        if score >= 70:
            return "REVIEW", "Likely plausible, but no evidence provided. Policy requires verification for high-liability/numeric."
        return "REVIEW", "No evidence provided for high-liability or numeric claim. Human verification recommended."

    # Thresholds by liability tier
    if liability == "low":
        if score >= 70:
            return "ALLOW", "High confidence per current MVP scoring."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."

    # High-liability
    if score >= 75:
        return "ALLOW", "High confidence with evidence under high-liability policy."
    elif score >= 55:
        return "REVIEW", "Medium confidence. Human verification recommended."
    return "BLOCK", "Low confidence. Do not use without verification."


# -------------------------
# Core verification routine (shared by /verify and /api/score)
# -------------------------
def run_verification(payload: dict):
    start = time.time()

    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()
    policy_mode = (payload.get("policy_mode") or DEFAULT_POLICY_MODE).strip()

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
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except TypeError:
            # If reference_engine doesn't accept evidence/policy_mode
            try:
                score, verdict, explanation = score_claim_text(text)
                score, verdict, explanation, signals, references = enrich_with_guardrails(
                    text, evidence, int(score), verdict, explanation
                )
            except Exception:
                score, verdict, explanation, signals, references = heuristic_score(text, evidence)
        except Exception:
            score, verdict, explanation, signals, references = heuristic_score(text, evidence)
    else:
        score, verdict, explanation, signals, references = heuristic_score(text, evidence)

    action, reason = decision_gate(int(score), signals)

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


# -------------------------
# Static landing page
# -------------------------
@app.get("/")
def landing():
    resp = make_response(send_from_directory(app.static_folder, "index.html"))
    return with_headers(resp)

@app.get("/static/<path:filename>")
def static_files(filename):
    resp = make_response(send_from_directory(app.static_folder, filename))
    return with_headers(resp)


# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    resp = make_response(jsonify({"status": "ok", "service": "trucite-backend", "ts": int(time.time())}))
    return with_headers(resp)


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
            "/verify": {
                "post": {
                    "summary": "Verify AI-generated output",
                    "description": "Returns reliability score and enforcement decision for AI output.",
                    "requestBody": {
                        "required": True,
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {
                                        "text": {"type": "string"},
                                        "evidence": {"type": "string"},
                                        "policy_mode": {"type": "string"}
                                    }
                                }
                            }
                        }
                    },
                    "responses": {"200": {"description": "Verification result"}}
                }
            },
            "/api/score": {
                "post": {
                    "summary": "Level-2 scoring endpoint",
                    "description": "Same as /verify but includes schema_version and latency_ms for infra-grade signaling.",
                    "responses": {"200": {"description": "Scoring result"}}
                }
            }
        }
    }
    resp = make_response(jsonify(spec))
    return with_headers(resp)


# -------------------------
# Verify endpoint (backward compatible)
# -------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    resp_obj, err = run_verification(payload)
    if err:
        msg, code = err
        resp = make_response(jsonify({"error": msg}), code)
        return with_headers(resp)

    resp = make_response(jsonify(resp_obj), 200)
    return with_headers(resp)


# -------------------------
# Level-2 JSON endpoint (infra / VC-facing)
# -------------------------
@app.route("/api/score", methods=["POST", "OPTIONS"])
def api_score():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    resp_obj, err = run_verification(payload)
    if err:
        msg, code = err
        resp = make_response(jsonify({"error": msg, "schema_version": SCHEMA_VERSION}), code)
        return with_headers(resp)

    resp = make_response(jsonify(resp_obj), 200)
    return with_headers(resp)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
