import os
import re
import json
import time
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# -----------------------------
# In-memory lightweight drift store (MVP)
# Keyed by a stable fingerprint of normalized input.
# NOTE: Render dynos can restart, so this is best-effort.
# -----------------------------
DRIFT_STORE: Dict[str, Dict[str, Any]] = {}

# -----------------------------
# Health
# -----------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "time_utc": utc_now_iso()}), 200


# -----------------------------
# Verify (POST only)
# -----------------------------
@app.post("/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body."}), 400

    # Normalize + fingerprint
    normalized = normalize_text(text)
    sha = sha256_hex(normalized)
    event_id = sha[:12]
    ts = utc_now_iso()

    # Claim extraction
    claims = extract_claims(text)
    claim_objs = []

    # Per-claim scoring
    per_scores = []
    risk_tags_union = set()

    for c in claims:
        c_score, c_verdict, c_tags = score_claim(c)
        per_scores.append(c_score)
        for t in c_tags:
            risk_tags_union.add(t)

        claim_objs.append({
            "text": c,
            "score": c_score,
            "verdict": c_verdict,
            "risk_tags": c_tags,
            "signals": derive_signals(c)
        })

    # Aggregate
    overall_score, overall_verdict = aggregate_score(per_scores, risk_tags_union)

    # Drift (best-effort)
    drift = compute_drift(sha, overall_score, overall_verdict, claim_objs, ts)

    response = {
        "event_id": event_id,
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": ts
        },
        "input": {
            "length_chars": len(text),
            "num_claims": len(claim_objs)
        },
        "verdict": overall_verdict,
        "score": overall_score,
        "explanation": (
            "MVP heuristic verification. TruCite does not 'prove truth' here; it flags risk using "
            "claim segmentation, uncertainty cues, citation/number patterns, and consistency signals. "
            "Enterprise mode adds evidence-backed checks and persistence."
        ),
        "claims": claim_objs,
        "uncertainty_map": {
            "risk_tags": sorted(list(risk_tags_union))
        },
        "drift": drift
    }

    return jsonify(response), 200


# -----------------------------
# Helpers
# -----------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_text(s: str) -> str:
    # Lowercase, collapse whitespace, strip punctuation noise (light normalization)
    s2 = s.lower()
    s2 = re.sub(r"\s+", " ", s2).strip()
    return s2


def extract_claims(text: str) -> List[str]:
    """
    MVP claim segmentation:
    - Splits on sentence boundaries and bullets
    - Keeps short meaningful clauses
    - De-duplicates
    """
    # Normalize newlines/bullets into separators
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"[\u2022•\-]\s+", "\n", t)  # bullets -> new line

    # Sentence split (simple heuristic)
    parts = re.split(r"(?<=[\.\?\!])\s+|\n+", t)
    cleaned = []
    seen = set()

    for p in parts:
        p = p.strip()
        if not p:
            continue
        # Filter out tiny fragments
        if len(p) < 12:
            continue
        # Remove trailing punctuation clutter
        p = p.strip(" \t\n-–—•")
        # De-dupe by normalized form
        k = normalize_text(p)
        if k in seen:
            continue
        seen.add(k)
        cleaned.append(p)

    # If everything got filtered, fallback to original as single "claim"
    if not cleaned:
        cleaned = [text.strip()]

    return cleaned[:30]  # cap for MVP safety


def derive_signals(claim: str) -> Dict[str, Any]:
    """
    Surface simple, explainable signals: numerics, citations-like patterns, hedges, absolutes.
    """
    c = claim
    numerics = re.findall(r"\b\d+(\.\d+)?\b", c)
    has_percent = bool(re.search(r"\b\d+(\.\d+)?\s*%\b", c))
    has_citation_like = bool(re.search(r"\b(v\.|vs\.|§|U\.S\.|F\.\d+d|WL\s*\d+|No\.\s*\d+)\b", c))
    has_url = "http://" in c or "https://" in c or "www." in c

    hedges = count_matches(c, [
        r"\bmay\b", r"\bmight\b", r"\bcould\b", r"\bpossible\b", r"\bpossibly\b",
        r"\bunclear\b", r"\bunknown\b", r"\bestimate\b", r"\bapprox\b", r"\bapproximately\b",
        r"\blikely\b", r"\bunlikely\b"
    ])
    absolutes = count_matches(c, [
        r"\balways\b", r"\bnever\b", r"\bguarantee\b", r"\bproves?\b", r"\bdefinitely\b",
        r"\b100%\b", r"\bmust\b"
    ])

    return {
        "has_numerics": len(numerics) > 0,
        "numeric_count": len(numerics),
        "has_percent": has_percent,
        "has_citation_like": has_citation_like,
        "has_url": has_url,
        "hedge_count": hedges,
        "absolute_count": absolutes
    }


def count_matches(text: str, patterns: List[str]) -> int:
    n = 0
    for p in patterns:
        if re.search(p, text, re.IGNORECASE):
            n += 1
    return n


def score_claim(claim: str) -> Tuple[int, str, List[str]]:
    """
    Returns: (0..100 score, verdict string, risk tags)
    Heuristics:
      - Overconfident absolute claims without citations => risk up
      - Legal/medical numeric/citation patterns => demand higher rigor
      - Hedges => reduce certainty (but not necessarily "false")
    """
    c = claim.strip()
    signals = derive_signals(c)

    base = 70  # start with "moderate"
    tags = []

    # Overconfidence penalty
    if signals["absolute_count"] >= 1:
        base -= 10
        tags.append("overconfident_language")

    # Hedging penalty (uncertainty)
    if signals["hedge_count"] >= 1:
        base -= 8
        tags.append("uncertainty_language")

    # Numeric claims need rigor
    if signals["has_numerics"]:
        base -= 8
        tags.append("numeric_claim")

    # Citation-like patterns: if present, we still can’t validate in MVP, but mark as citation-sensitive
    if signals["has_citation_like"]:
        base -= 6
        tags.append("citation_sensitive")

    # URLs imply "source present" but still unverified
    if signals["has_url"]:
        base += 3
        tags.append("source_link_present")

    # Extremely short/long claims are riskier for reliable interpretation
    if len(c) < 25:
        base -= 6
        tags.append("too_short")
    if len(c) > 240:
        base -= 6
        tags.append("too_long")

    # Clamp
    score = int(max(0, min(100, base)))

    # Verdict mapping (MVP)
    if score >= 80:
        verdict = "Likely OK (still verify sources)"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk / likely unreliable"

    return score, verdict, tags


def aggregate_score(scores: List[int], tags: set) -> Tuple[int, str]:
    if not scores:
        return 50, "Unclear / needs verification"

    avg = sum(scores) / len(scores)

    # If certain risk tags exist, reduce overall confidence
    if "citation_sensitive" in tags and "overconfident_language" in tags:
        avg -= 6
    if "numeric_claim" in tags and "overconfident_language" in tags:
        avg -= 6

    overall = int(max(0, min(100, round(avg))))

    if overall >= 80:
        verdict = "Likely OK (still verify sources)"
    elif overall >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk / likely unreliable"

    return overall, verdict


def compute_drift(key_sha: str, score: int, verdict: str, claims: List[Dict[str, Any]], ts: str) -> Dict[str, Any]:
    """
    Best-effort drift signal:
    - Compare this run to last run for same normalized input hash
    - Also track a rolling history length
    """
    prev = DRIFT_STORE.get(key_sha)
    drift = {
        "has_prior": False,
        "prior_timestamp_utc": None,
        "score_delta": None,
        "verdict_changed": False,
        "claim_count_delta": None,
        "drift_flag": False,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares workflow-level behavior over time."
    }

    if prev:
        drift["has_prior"] = True
        drift["prior_timestamp_utc"] = prev.get("timestamp_utc")
        drift["score_delta"] = score - int(prev.get("score", 0))
        drift["verdict_changed"] = (verdict != prev.get("verdict"))
        drift["claim_count_delta"] = len(claims) - int(prev.get("num_claims", 0))

        # Simple drift rule: >10 score change or verdict flip
        if abs(drift["score_delta"]) >= 10 or drift["verdict_changed"]:
            drift["drift_flag"] = True

    # Store latest
    DRIFT_STORE[key_sha] = {
        "timestamp_utc": ts,
        "score": score,
        "verdict": verdict,
        "num_claims": len(claims)
    }

    return drift


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
