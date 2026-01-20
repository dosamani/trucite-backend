from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# ----------------------------
# In-memory drift store (MVP)
# Keyed by sha256(input_text)
# ----------------------------
DRIFT_STORE: Dict[str, Dict[str, Any]] = {}


# ----------------------------
# Helpers
# ----------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def short_event_id(sha256_: str) -> str:
    return sha256_[:12]


HEDGE_WORDS = [
    "may", "might", "could", "possibly", "likely", "unlikely", "suggests",
    "appears", "seems", "roughly", "approximately", "estimated", "estimate"
]

# Basic patterns (MVP)
URL_RE = re.compile(r"(https?://\S+)", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
PERCENT_RE = re.compile(r"\b\d+(\.\d+)?\s*%")
NUMERIC_RE = re.compile(r"\b\d+(\.\d+)?\b")
CITATION_LIKE_RE = re.compile(r"(\[\d+\]|\(\s*\d{4}\s*\)|et al\.)", re.IGNORECASE)

ABSOLUTE_RE = re.compile(r"\b(always|never|guaranteed|proves|definitely|certainly)\b", re.IGNORECASE)


def count_hedges(text: str) -> int:
    t = text.lower()
    return sum(t.count(w) for w in HEDGE_WORDS)


def tokenize_claims(text: str) -> List[str]:
    """
    MVP claim segmentation:
    - Split by newline, period, semicolon, bullet-like separators
    - Keep short list and filter empties
    """
    cleaned = re.sub(r"\s+", " ", text.strip())
    if not cleaned:
        return []
    # split on sentence-ish boundaries
    parts = re.split(r"(?<=[\.\!\?;])\s+|(?:\n+)|(?:\s+\-\s+)", cleaned)
    claims = [p.strip(" -\t") for p in parts if p and p.strip()]
    # collapse overly long items into one claim
    return claims[:12]  # MVP limit


def classify_claim(text: str) -> str:
    """Rudimentary claim type classifier (MVP)."""
    has_num = bool(NUMERIC_RE.search(text))
    has_pct = bool(PERCENT_RE.search(text))
    has_year = bool(YEAR_RE.search(text))
    if has_num or has_pct or has_year:
        return "numeric_or_stat_claim"
    if " is " in text.lower() or " are " in text.lower():
        return "descriptive_claim"
    return "general_claim"


def build_verification_query(claim: str) -> str:
    """
    Produce a suggested query string the user can copy into search,
    or into a retrieval system.
    """
    # Strip noisy punctuation
    q = re.sub(r"[\"“”]", "", claim).strip()
    # If it's medical-ish, nudge toward evidence terms (still generic)
    if re.search(r"\b(aspirin|mi|myocardial|stroke|risk|mortality|trial)\b", q, re.IGNORECASE):
        return f'{q} clinical trial meta-analysis PMID'
    return f'{q} source DOI'


@dataclass
class ClaimScore:
    text: str
    score: int
    verdict: str
    risk_tags: List[str]
    claim_type: str
    signals: Dict[str, Any]
    evidence_needed: Optional[Dict[str, Any]] = None


def score_claim(claim: str, provided_sources: Optional[List[str]] = None) -> ClaimScore:
    """
    MVP heuristic scoring:
    - Start at 78
    - Penalize for numerics without sources
    - Penalize for citation-like patterns without URL/DOI
    - Penalize for absolutes
    - Slightly adjust for hedging (a bit safer language)
    """
    base = 78
    risk_tags: List[str] = []
    signals: Dict[str, Any] = {}

    has_url = bool(URL_RE.search(claim))
    has_doi = bool(DOI_RE.search(claim))
    has_year = bool(YEAR_RE.search(claim))
    has_percent = bool(PERCENT_RE.search(claim))
    numeric_count = len(NUMERIC_RE.findall(claim))
    hedge_count = count_hedges(claim)
    abs_count = len(ABSOLUTE_RE.findall(claim))
    has_citation_like = bool(CITATION_LIKE_RE.search(claim))

    claim_type = classify_claim(claim)

    signals.update({
        "has_url": has_url,
        "has_doi": has_doi,
        "has_year": has_year,
        "has_percent": has_percent,
        "has_numerics": numeric_count > 0,
        "numeric_count": numeric_count,
        "hedge_count": hedge_count,
        "absolute_count": abs_count,
        "has_citation_like": has_citation_like,
        "has_sources_provided": bool(provided_sources),
    })

    score = base

    # Absolutes are a risk signal
    if abs_count > 0:
        risk_tags.append("absolute_language")
        score -= min(10, 4 + abs_count)

    # Citation-like without URL/DOI/source is risky
    if has_citation_like and not (has_url or has_doi or provided_sources):
        risk_tags.append("citation_unverified")
        score -= 10

    # Numeric/stat claims are risky without sources
    if claim_type == "numeric_or_stat_claim":
        risk_tags.append("numeric_claim")
        # penalize stronger if no evidence anchor
        if not (has_url or has_doi or provided_sources):
            score -= 16
        else:
            score -= 6  # still needs checking, but better

    # Hedges: slightly reduce risk (language acknowledges uncertainty)
    if hedge_count > 0:
        risk_tags.append("hedged_language")
        score += min(4, hedge_count)  # small bump

    # Clamp
    score = max(0, min(100, int(round(score))))

    # Verdict logic (MVP)
    if score >= 86:
        verdict = "Likely reliable (still verify for production)"
    elif 70 <= score <= 85:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk / do not rely"

    evidence_needed = None
    # Evidence-needed hooks (MVP)
    if (claim_type == "numeric_or_stat_claim" or has_citation_like) and not (has_url or has_doi or provided_sources):
        evidence_needed = {
            "required": True,
            "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI) or provided evidence.",
            "suggested_query": build_verification_query(claim),
            "acceptable_evidence_examples": [
                "Peer-reviewed paper link (DOI/PMID/URL)",
                "Clinical guideline link (e.g., society guideline URL)",
                "Regulatory label / official statement URL",
                "Internal policy document reference (enterprise mode)",
            ],
        }

    return ClaimScore(
        text=claim,
        score=score,
        verdict=verdict,
        risk_tags=sorted(list(set(risk_tags))),
        claim_type=claim_type,
        signals=signals,
        evidence_needed=evidence_needed,
    )


def aggregate_score(claims: List[ClaimScore]) -> Tuple[int, str]:
    if not claims:
        return 0, "No input"
    # Weighted average: numeric claims slightly heavier
    weights = []
    for c in claims:
        w = 1.2 if c.claim_type == "numeric_or_stat_claim" else 1.0
        weights.append(w)
    total_w = sum(weights)
    score = sum(c.score * w for c, w in zip(claims, weights)) / total_w
    score_i = int(round(score))

    # Aggregate verdict based on worst claim + overall score
    worst = min(c.score for c in claims)
    if worst < 60 or score_i < 70:
        verdict = "Unclear / needs verification"
    if worst < 50 or score_i < 60:
        verdict = "High risk / do not rely"
    if score_i >= 86 and worst >= 75:
        verdict = "Likely reliable (still verify for production)"
    return score_i, verdict


def compute_drift(input_hash: str, current: Dict[str, Any]) -> Dict[str, Any]:
    """
    MVP drift:
    - compares only to last run for same input_hash in memory
    """
    prior = DRIFT_STORE.get(input_hash)
    if not prior:
        DRIFT_STORE[input_hash] = current
        return {
            "has_prior": False,
            "prior_timestamp_utc": None,
            "score_delta": None,
            "claim_count_delta": None,
            "verdict_changed": False,
            "drift_flag": False,
            "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
        }

    score_delta = current.get("score", 0) - prior.get("score", 0)
    claim_count_delta = current.get("input", {}).get("num_claims", 0) - prior.get("input", {}).get("num_claims", 0)
    verdict_changed = current.get("verdict") != prior.get("verdict")

    # drift flag heuristic
    drift_flag = verdict_changed or abs(score_delta) >= 12 or abs(claim_count_delta) >= 2

    DRIFT_STORE[input_hash] = current
    return {
        "has_prior": True,
        "prior_timestamp_utc": prior.get("audit_fingerprint", {}).get("timestamp_utc"),
        "score_delta": score_delta,
        "claim_count_delta": claim_count_delta,
        "verdict_changed": verdict_changed,
        "drift_flag": drift_flag,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
    }


# ----------------------------
# Routes
# ----------------------------
@app.get("/")
def home():
    # Serve static index if present; otherwise simple message
    try:
        return send_from_directory("static", "index.html")
    except Exception:
        return (
            "<h3>TruCite backend is running.</h3>"
            "<p>POST /score with JSON: {\"text\": \"...\"}</p>",
            200,
        )


@app.post("/score")
def score():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    # Optional evidence sources user can pass
    sources = payload.get("sources")
    if sources is not None and not isinstance(sources, list):
        sources = None

    if not text:
        return jsonify({"error": "Missing 'text'"}), 400

    input_hash = sha256_hex(text)
    event_id = short_event_id(input_hash)

    claims_raw = tokenize_claims(text)
    claims_scored = [score_claim(c, provided_sources=sources) for c in claims_raw]

    score_i, verdict = aggregate_score(claims_scored)

    response: Dict[str, Any] = {
        "audit_fingerprint": {
            "sha256": input_hash,
            "timestamp_utc": utc_now_iso(),
        },
        "event_id": event_id,
        "input": {
            "length_chars": len(text),
            "num_claims": len(claims_scored),
        },
        "score": score_i,
        "verdict": verdict,
        "claims": [asdict(c) for c in claims_scored],
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, "
            "numeric/stat patterns, citation signals, absolute language, and uncertainty cues. "
            "Enterprise mode adds evidence-backed checks, source validation, and persistent drift analytics."
        ),
        # Keep uncertainty_map for your current UI expectations
        "uncertainty_map": {
            "risk_tags": sorted(list({t for c in claims_scored for t in c.risk_tags})),
        },
    }

    # Drift (MVP)
    drift = compute_drift(input_hash, response)
    response["drift"] = drift

    return jsonify(response), 200


@app.get("/health")
def health():
    return jsonify({"status": "ok", "time_utc": utc_now_iso()}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
