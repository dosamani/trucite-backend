import os
import time
import json
import uuid
import hashlib
import re
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
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
# Phase 1 Policy + Versioning
# -------------------------
POLICY_VERSION = "2026.01"
POLICY_MODE_DEFAULT = os.environ.get("POLICY_MODE_DEFAULT", "enterprise").strip()

# -------------------------
# Phase 1 Limits (API hygiene)
# -------------------------
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(48 * 1024)))  # 48KB
MAX_TEXT_CHARS = int(os.environ.get("MAX_TEXT_CHARS", "6000"))
MAX_EVIDENCE_CHARS = int(os.environ.get("MAX_EVIDENCE_CHARS", "6000"))

# -------------------------
# Phase 1 Simple Rate Limit (in-memory, per-IP)
# -------------------------
RATE_LIMIT_WINDOW_SEC = int(os.environ.get("RATE_LIMIT_WINDOW_SEC", "60"))
RATE_LIMIT_MAX_REQ = int(os.environ.get("RATE_LIMIT_MAX_REQ", "30"))
_ip_hits = {}  # ip -> [timestamps]

# -------------------------
# Step 8 Telemetry (no DB)
# -------------------------
BOOT_TS = int(time.time())

_metrics = {
    "boot_ts": BOOT_TS,
    "requests_total": 0,
    "verify_calls_total": 0,
    "verify_success_total": 0,
    "verify_4xx_total": 0,
    "verify_5xx_total": 0,
    "decision_allow_total": 0,
    "decision_review_total": 0,
    "decision_block_total": 0,
    "avg_verify_latency_ms": 0.0,
    "last_verify_latency_ms": 0.0,
    "last_event_id": None,
    "last_decision": None,
    "last_score": None,
}

LAT_EMA_ALPHA = float(os.environ.get("LAT_EMA_ALPHA", "0.15"))

# -------------------------
# Guardrails + parsing helpers
# -------------------------
UNIVERSAL_CERTAINTY_TERMS = [
    "always", "never", "guaranteed", "definitely", "proves", "proof", "100%", "cures", "cure", "no doubt"
]

# Keep tiny + obvious for demo credibility
KNOWN_FALSE_PATTERNS = [
    r"\bthe\s+earth\s+is\s+flat\b",
    r"\bearth\s+is\s+flat\b",
    r"\bflat\s+earth\b",
    r"\bvaccines?\s+cause\s+autism\b",
    r"\b5g\s+causes?\s+covid\b",
    r"\bmoon\s+landing\s+was\s+fake\b",
]

# High-liability keyword hints (MVP heuristic, not advice)
HIGH_LIABILITY_KEYWORDS = [
    # medical
    "dose", "dosage", "mg", "mcg", "units", "diagnosis", "treat", "treatment", "contraindication", "side effect",
    "guideline", "clinical", "patient", "prescribe", "medication", "drug", "insulin", "warfarin", "aspirin",
    # legal
    "contract", "liability", "lawsuit", "indemnify", "breach", "statute", "jurisdiction", "legal advice",
    # finance
    "roi", "interest rate", "apr", "yield", "stock", "market", "earnings", "arr", "revenue", "forecast",
    "valuation", "tax", "irs"
]
def policy_hash() -> str:
    """
    A stable hash representing the Phase 1 policy surface.
    Useful for acquirers/audit: "what policy produced this decision?"
    """
    policy_obj = {
        "policy_version": POLICY_VERSION,
        "known_false_patterns": KNOWN_FALSE_PATTERNS,
        "universal_certainty_terms": UNIVERSAL_CERTAINTY_TERMS,
        "high_liability_keywords": HIGH_LIABILITY_KEYWORDS,
        "thresholds": {
            "low_allow": 70,
            "low_review": 55,
            "high_allow": 75,
            "high_review": 55,
            "known_false_cap": 45,
            "universal_no_evidence_cap": 60,
            "high_liability_no_evidence_cap": 73,
        },
        "limits": {
            "max_body_bytes": MAX_BODY_BYTES,
            "max_text_chars": MAX_TEXT_CHARS,
            "max_evidence_chars": MAX_EVIDENCE_CHARS,
        },
        "rate_limit": {
            "window_sec": RATE_LIMIT_WINDOW_SEC,
            "max_req": RATE_LIMIT_MAX_REQ,
        }
    }
    blob = json.dumps(policy_obj, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:12]


def _client_ip() -> str:
    # Render / proxies often set X-Forwarded-For. Use first hop.
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        return xff.split(",")[0].strip()
    return (request.remote_addr or "unknown").strip()


def _rate_limit_check(ip: str) -> bool:
    """
    Returns True if allowed, False if rate-limited.
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW_SEC
    hits = _ip_hits.get(ip, [])
    hits = [t for t in hits if t >= window_start]
    if len(hits) >= RATE_LIMIT_MAX_REQ:
        _ip_hits[ip] = hits
        return False
    hits.append(now)
    _ip_hits[ip] = hits
    return True


def _update_latency(ms: float):
    _metrics["last_verify_latency_ms"] = float(ms)
    current = float(_metrics.get("avg_verify_latency_ms", 0.0))
    if current <= 0:
        _metrics["avg_verify_latency_ms"] = float(ms)
    else:
        _metrics["avg_verify_latency_ms"] = (1 - LAT_EMA_ALPHA) * current + LAT_EMA_ALPHA * float(ms)


def _incr_decision(action: str):
    a = (action or "").upper()
    if a == "ALLOW":
        _metrics["decision_allow_total"] += 1
    elif a == "REVIEW":
        _metrics["decision_review_total"] += 1
    elif a == "BLOCK":
        _metrics["decision_block_total"] += 1


@app.before_request
def count_requests():
    _metrics["requests_total"] += 1


@app.before_request
def enforce_body_size_and_rate_limit():
    # Body size guardrail (basic API hygiene)
    if request.content_length is not None and request.content_length > MAX_BODY_BYTES:
        if request.path == "/verify":
            _metrics["verify_calls_total"] += 1
            _metrics["verify_4xx_total"] += 1
        return jsonify({"error": "Payload too large", "max_bytes": MAX_BODY_BYTES}), 413

    # Rate limit only on verify (keep landing page fast)
    if request.path == "/verify" and request.method in ("POST", "OPTIONS"):
        if request.method == "OPTIONS":
            return None
        ip = _client_ip()
        ok = _rate_limit_check(ip)
        if not ok:
            _metrics["verify_calls_total"] += 1
            _metrics["verify_4xx_total"] += 1
            return jsonify({"error": "Rate limited", "window_sec": RATE_LIMIT_WINDOW_SEC, "max_req": RATE_LIMIT_MAX_REQ}), 429

    return None


@app.after_request
def attach_request_id(resp):
    rid = request.headers.get("X-Request-Id")
    if not rid:
        rid = uuid.uuid4().hex[:12]
    resp.headers["X-Request-Id"] = rid
    resp.headers["X-TruCite-Policy-Version"] = POLICY_VERSION
    resp.headers["X-TruCite-Policy-Hash"] = policy_hash()
    return resp


# -------------------------
# Static landing page
# -------------------------
@app.get("/")
def landing():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    return jsonify({
        "status": "ok",
        "service": "trucite-backend",
        "ts": int(time.time()),
        "policy_version": POLICY_VERSION,
        "policy_hash": policy_hash(),
    })


# -------------------------
# OpenAPI stub (API credibility signal)
# -------------------------
@app.get("/openapi.json")
def openapi_spec():
    return jsonify({
        "openapi": "3.0.0",
        "info": {
            "title": "TruCite Verification API",
            "version": POLICY_VERSION,
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
                                    },
                                    "required": ["text"]
                                }
                            }
                        }
                    },
                    "responses": {
                        "200": {"description": "Verification result"},
                        "400": {"description": "Bad request"},
                        "413": {"description": "Payload too large"},
                        "429": {"description": "Rate limited"}
                    }
                }
            },
            "/health": {"get": {"summary": "Health check", "responses": {"200": {"description": "OK"}}}},
            "/metrics": {"get": {"summary": "Telemetry metrics", "responses": {"200": {"description": "OK"}}}},
            "/status": {"get": {"summary": "Safe status snapshot", "responses": {"200": {"description": "OK"}}}},
        }
    })
    # -------------------------
# Verify endpoint
# -------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return ("", 204)

    _metrics["verify_calls_total"] += 1
    t0 = time.time()

    try:
        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        evidence = (payload.get("evidence") or "").strip()
        policy_mode = (payload.get("policy_mode") or POLICY_MODE_DEFAULT).strip()

        if not text:
            _metrics["verify_4xx_total"] += 1
            return jsonify({"error": "Missing 'text' in request body"}), 400

        if len(text) > MAX_TEXT_CHARS:
            _metrics["verify_4xx_total"] += 1
            return jsonify({"error": "Text too large", "max_chars": MAX_TEXT_CHARS}), 413

        if evidence and len(evidence) > MAX_EVIDENCE_CHARS:
            _metrics["verify_4xx_total"] += 1
            return jsonify({"error": "Evidence too large", "max_chars": MAX_EVIDENCE_CHARS}), 413

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
                # backwards-compatible signature
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

        # Decision Gate (uses signals)
        action, reason = decision_gate(int(score), signals)

        resp = {
            "verdict": verdict,
            "score": int(score),
            "decision": {"action": action, "reason": reason},
            "event_id": event_id,
            "policy_mode": policy_mode,
            "policy_version": POLICY_VERSION,
            "policy_hash": policy_hash(),
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": ts},
            "claims": claims,
            "references": references,
            "signals": signals,
            "explanation": explanation
        }

        # latency + metrics
        elapsed_ms = (time.time() - t0) * 1000.0
        _update_latency(elapsed_ms)

        _metrics["verify_success_total"] += 1
        _metrics["last_event_id"] = event_id
        _metrics["last_decision"] = action
        _metrics["last_score"] = int(score)
        _incr_decision(action)

        r = jsonify(resp)
        r.headers["X-TruCite-Latency-ms"] = str(int(elapsed_ms))
        return r, 200

    except Exception:
        _metrics["verify_5xx_total"] += 1
        raise


# -------------------------
# Decision logic
# -------------------------
def decision_gate(score: int, signals: dict):
    """
    Demo policy (good for MVP / acqui-hire optics):
      - Known-debunked categories: BLOCK unless evidence
      - High-liability or numeric claims: evidence required to reach ALLOW
      - If high-liability + no evidence + low score -> BLOCK (harm prevention)
      - Low-liability non-numeric: can ALLOW at a lower threshold (>=70)
    """
    guardrail = signals.get("guardrail")
    has_refs = bool(signals.get("has_references"))
    liability = (signals.get("liability_tier") or "low").lower()
    evidence_required_for_allow = bool(signals.get("evidence_required_for_allow"))

    # Hard guardrails first
    if guardrail == "known_false_claim_no_evidence":
        return "BLOCK", "Known false / widely debunked category without evidence. Demo guardrail triggered."
    if guardrail == "unsupported_universal_claim_no_evidence":
        return "REVIEW", "Unsupported universal/high-certainty claim without evidence. Conservative gating applied."

    # High-liability evidence enforcement
    if evidence_required_for_allow and not has_refs:
        if score >= 70:
            return "REVIEW", "Likely true, but no evidence provided. Conservative demo policy requires human verification."
        # If low confidence in high-liability with no evidence, BLOCK to prevent downstream harm
        return "BLOCK", "Low confidence + no evidence for high-liability or numeric claim. Blocked to prevent downstream harm."

    # Thresholds by liability tier
    if liability == "low":
        if score >= 70:
            return "ALLOW", "High confidence per current MVP scoring."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."
    else:
        # High-liability: higher bar even with evidence
        if score >= 75:
            return "ALLOW", "High confidence with evidence under high-liability policy."
        elif score >= 55:
            return "REVIEW", "Medium confidence. Human verification recommended."
        return "BLOCK", "Low confidence. Do not use without verification."


# -------------------------
# Guardrails + parsing helpers
# -------------------------
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


def liability_tier(text: str) -> str:
    tl = normalize_text(text)
    if has_any_digit(text):
        return "high"
    for kw in HIGH_LIABILITY_KEYWORDS:
        if kw in tl:
            return "high"
    return "low"


def enrich_with_guardrails(text: str, evidence: str, score: int, verdict: str, explanation: str):
    base_score = int(max(0, min(100, score)))
    s, v, e, signals, references = heuristic_score(text, evidence, seed_score=base_score)
    if verdict and isinstance(verdict, str):
        v = verdict
    if explanation and isinstance(explanation, str):
        e = explanation
    return s, v, e, signals, references
    # -------------------------
# MVP heuristic scoring + guardrails
# -------------------------
def heuristic_score(text: str, evidence: str = "", seed_score: int = 55):
    """
    MVP heuristic scoring (0-100) + conservative guardrails.
    - Scores linguistic certainty/uncertainty + risk signals
    - Evidence boosts only when present (URL/DOI/PMID)
    - High-liability or numeric claims require evidence to reach ALLOW (enforced in decision_gate)
    - Tiny demo guardrail list for widely debunked categories
    """

    raw = (text or "")
    t = raw.strip()
    tl = normalize_text(t)
    ev = (evidence or "").strip()

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
        rules_fired.append("short_declarative_claim_bonus")
        score += 18

    # Guardrail #1: Known false categories
    if matches_known_false(t) and not has_refs:
        score = min(score, 45)
        risk_flags.append("known_false_category_no_evidence")
        rules_fired.append("known_false_category_cap")
        guardrail = "known_false_claim_no_evidence"
    else:
        guardrail = None

    # Guardrail #2: Unsupported universal/high-certainty claims w/out evidence
    if (short_decl and contains_universal_certainty(t)) and not has_refs and guardrail is None:
        score = min(score, 60)
        risk_flags.append("unsupported_universal_claim_no_evidence")
        rules_fired.append("unsupported_universal_claim_cap")
        guardrail = "unsupported_universal_claim_no_evidence"

    # Evidence helps, but doesn't guarantee ALLOW
    if has_refs:
        score += 5
        risk_flags.append("evidence_present")
        rules_fired.append("evidence_present_bonus")

    # Conservative cap for high-liability without evidence (pre-gate)
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
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, basic risk signals, "
        "and applies conservative handling for numeric or liability claims unless evidence is provided. "
        "It also includes lightweight guardrails to prevent obvious debunked categories and unsupported universal claims "
        "from being ALLOWed without evidence. Replace with evidence-backed verification in production."
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


# -------------------------
# Step 8: Metrics endpoint
# -------------------------
@app.get("/metrics")
def metrics():
    uptime_sec = int(time.time()) - int(_metrics["boot_ts"])
    out = dict(_metrics)
    out["uptime_sec"] = uptime_sec

    vc = max(1, int(out.get("verify_calls_total", 0)))
    out["decisions_total"] = (
        int(out.get("decision_allow_total", 0)) +
        int(out.get("decision_review_total", 0)) +
        int(out.get("decision_block_total", 0))
    )
    out["avg_verify_latency_ms"] = round(float(out.get("avg_verify_latency_ms", 0.0)), 2)
    out["last_verify_latency_ms"] = round(float(out.get("last_verify_latency_ms", 0.0)), 2)

    out["verify_success_rate"] = round(int(out.get("verify_success_total", 0)) / vc, 4)
    out["verify_error_rate"] = round((int(out.get("verify_4xx_total", 0)) + int(out.get("verify_5xx_total", 0))) / vc, 4)

    return jsonify(out)


# -------------------------
# Step 8: Safe status snapshot
# -------------------------
@app.get("/status")
def status():
    safe = {
        "service": "trucite-backend",
        "status": "ok",
        "ts": int(time.time()),
        "uptime_sec": int(time.time()) - BOOT_TS,
        "policy": {
            "mode_default": POLICY_MODE_DEFAULT,
            "version": POLICY_VERSION,
            "hash": policy_hash(),
        },
        "limits": {
            "rate_limit_window_sec": RATE_LIMIT_WINDOW_SEC,
            "rate_limit_max_req": RATE_LIMIT_MAX_REQ,
            "max_text_chars": MAX_TEXT_CHARS,
            "max_evidence_chars": MAX_EVIDENCE_CHARS,
            "max_body_bytes": MAX_BODY_BYTES,
        },
        "build": {
            "commit": os.environ.get("GIT_COMMIT"),
            "region": os.environ.get("RENDER_REGION"),
        }
    }
    return jsonify(safe)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
