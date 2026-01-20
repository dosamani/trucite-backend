import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory

# -----------------------------
# Flask setup
# -----------------------------
app = Flask(__name__, static_folder="static")

# Ensure Render uses this port
PORT = int(os.environ.get("PORT", 10000))

# -----------------------------
# Helpers
# -----------------------------
ABSOLUTE_TERMS = [
    "always", "never", "guaranteed", "proven", "definitely", "certainly",
    "undeniably", "everyone knows", "no doubt", "100%", "must be"
]

HEDGE_TERMS = [
    "may", "might", "could", "possibly", "likely", "appears", "suggests",
    "unclear", "unknown", "not sure", "needs verification", "cannot confirm"
]

CITATION_LIKE = re.compile(r"\b(19|20)\d{2}\b|\bPMID\b|\bDOI\b|\bvs\.\b|\bv\.\b|\bWL\b|\bF\.\d+d\b|\bU\.S\.\b", re.IGNORECASE)
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PERCENT_RE = re.compile(r"\b\d+(\.\d+)?%\b")
NUMERIC_RE = re.compile(r"\b\d+(\.\d+)?\b")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def count_terms(text: str, terms) -> int:
    t = text.lower()
    return sum(1 for term in terms if term in t)


def segment_claims(text: str):
    """
    Lightweight claim segmentation:
    Splits on line breaks, bullets, and sentence-ish delimiters.
    """
    raw = re.split(r"[\n\r]+|•|- |\u2022|(?<=[.?!;])\s+", text.strip())
    claims = [c.strip() for c in raw if c and c.strip()]
    # Cap to avoid huge payloads
    return claims[:12]


# In-memory drift store (MVP)
# key: sha256(text) => {"score": int, "verdict": str, "ts": iso}
DRIFT_STORE = {}


def analyze_claim(claim: str):
    lc = claim.strip()
    abs_count = count_terms(lc, ABSOLUTE_TERMS)
    hedge_count = count_terms(lc, HEDGE_TERMS)

    has_url = bool(URL_RE.search(lc))
    has_doi = bool(DOI_RE.search(lc))
    has_citation_like = bool(CITATION_LIKE.search(lc))

    nums = NUMERIC_RE.findall(lc)
    numeric_count = len(nums)
    has_numerics = numeric_count > 0
    has_percent = bool(PERCENT_RE.search(lc))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", lc))

    # Risk tags
    risk_tags = []
    claim_type = "general_claim"

    if has_numerics or has_percent or has_year:
        risk_tags.append("numeric_claim")
        claim_type = "numeric_or_stat_claim"

    # If it looks like a citation but no URL/DOI provided, flag
    has_sources_provided = has_url or has_doi
    if has_citation_like and not has_sources_provided:
        risk_tags.append("citation_unverified")

    if abs_count >= 1:
        risk_tags.append("absolute_language")

    if len(lc) > 240:
        risk_tags.append("long_claim")

    # Scoring heuristic (0..100, higher = safer)
    score = 72

    # Penalize numeric/citation claims without evidence
    if "numeric_claim" in risk_tags:
        score -= 12
    if "citation_unverified" in risk_tags:
        score -= 10

    # Absolute language penalizes
    if "absolute_language" in risk_tags:
        score -= 8

    # Hedge language slightly improves (signals uncertainty)
    if hedge_count >= 1:
        score += 4

    # URL/DOI improves
    if has_sources_provided:
        score += 8

    # Clamp
    score = max(0, min(100, score))

    # Verdict bands
    if score >= 80:
        verdict = "Low risk / likely reliable"
    elif score >= 60:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk / do not rely"

    evidence_needed = None
    if claim_type == "numeric_or_stat_claim" and not has_sources_provided:
        evidence_needed = {
            "required": True,
            "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI) or provided evidence.",
            "acceptable_evidence_examples": [
                "Peer-reviewed paper link (DOI/PMID/URL)",
                "Clinical guideline link (e.g., society guideline URL)",
                "Regulatory label / official statement URL",
                "Internal policy document reference (enterprise mode)"
            ],
            "suggested_query": f"{lc} clinical trial meta-analysis PMID"
        }

    return {
        "text": lc,
        "score": score,
        "verdict": verdict,
        "risk_tags": risk_tags,
        "claim_type": claim_type,
        "signals": {
            "absolute_count": abs_count,
            "hedge_count": hedge_count,
            "has_url": has_url,
            "has_doi": has_doi,
            "has_citation_like": has_citation_like,
            "has_numerics": has_numerics,
            "numeric_count": numeric_count,
            "has_percent": has_percent,
            "has_year": has_year,
            "has_sources_provided": has_sources_provided
        },
        "evidence_needed": evidence_needed
    }


def drift_check(text_hash: str, score: int, verdict: str):
    prior = DRIFT_STORE.get(text_hash)
    if not prior:
        DRIFT_STORE[text_hash] = {"score": score, "verdict": verdict, "ts": utc_now_iso()}
        return {
            "has_prior": False,
            "drift_flag": False,
            "score_delta": None,
            "verdict_changed": False,
            "prior_timestamp_utc": None,
            "claim_count_delta": None,
            "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time."
        }

    score_delta = score - prior["score"]
    verdict_changed = verdict != prior["verdict"]
    drift_flag = verdict_changed or abs(score_delta) >= 15

    # Update stored
    DRIFT_STORE[text_hash] = {"score": score, "verdict": verdict, "ts": utc_now_iso()}

    return {
        "has_prior": True,
        "drift_flag": drift_flag,
        "score_delta": score_delta,
        "verdict_changed": verdict_changed,
        "prior_timestamp_utc": prior["ts"],
        "claim_count_delta": None,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time."
    }


# -----------------------------
# Routes
# -----------------------------

# Serve your landing page from /static/index.html
@app.get("/")
def home():
    return send_from_directory(app.static_folder, "index.html")


# Explicit static route (helps avoid Render config surprises)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.get("/health")
def health():
    return jsonify(ok=True)


# Allow OPTIONS to prevent Method Not Allowed in some browsers/proxies
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input",
            "explanation": "Provide text to verify.",
            "claims": [],
            "input": {"length_chars": 0, "num_claims": 0},
            "audit_fingerprint": {"sha256": sha256_hex(""), "timestamp_utc": utc_now_iso()},
            "event_id": sha256_hex("")[:12],
            "drift": {
                "has_prior": False,
                "drift_flag": False,
                "notes": "No text provided."
            }
        }), 200

    fp = sha256_hex(text)
    event_id = fp[:12]

    claims_text = segment_claims(text)
    claims = [analyze_claim(c) for c in claims_text]

    # Overall score: average of claim scores
    if claims:
        overall_score = round(sum(c["score"] for c in claims) / len(claims))
    else:
        overall_score = 0

    # Overall verdict: worst-case (more conservative)
    verdicts = [c["verdict"] for c in claims]
    if "High risk / do not rely" in verdicts:
        overall_verdict = "High risk / do not rely"
    elif "Unclear / needs verification" in verdicts:
        overall_verdict = "Unclear / needs verification"
    else:
        overall_verdict = "Low risk / likely reliable"

    drift = drift_check(fp, overall_score, overall_verdict)

    out = {
        "event_id": event_id,
        "score": overall_score,
        "verdict": overall_verdict,
        "claims": claims,
        "uncertainty_map": {
            "risk_tags": list({t for c in claims for t in (c.get("risk_tags") or [])})
        },
        "input": {"length_chars": len(text), "num_claims": len(claims)},
        "drift": drift,
        "audit_fingerprint": {"sha256": fp, "timestamp_utc": utc_now_iso()},
        "explanation": "MVP heuristic verification. This demo flags risk via claim segmentation, numeric/stat patterns, citation signals, absolute language, and uncertainty cues. Enterprise mode adds evidence-backed checks, source validation, and persistent drift analytics."
    }

    return jsonify(out), 200

@app.get("/routes")
def routes():
    return jsonify(sorted([str(r) for r in app.url_map.iter_rules()]))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT)
