import os
import re
import hashlib
from datetime import datetime, timezone
from typing import List, Dict, Any, Tuple

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# In-memory drift store (MVP)
DRIFT_STORE: Dict[str, Dict[str, Any]] = {}


# ------------------------------------------------------------------
# ROOT ROUTE — will always return something (never 404)
# ------------------------------------------------------------------
@app.get("/")
def home():
    """
    Serve landing page if present:
      - static/index.html (preferred)
      - index.html in repo root (fallback)
    Otherwise return a simple alive banner so / never 404s.
    """

    # Preferred: /static/index.html
    static_dir = app.static_folder  # "static"
    static_index = os.path.join(static_dir, "index.html")
    if os.path.exists(static_index):
        return send_from_directory(static_dir, "index.html")

    # Fallback: /index.html at repo root
    root_dir = os.getcwd()
    root_index = os.path.join(root_dir, "index.html")
    if os.path.exists(root_index):
        return send_from_directory(root_dir, "index.html")

    # Final fallback: never 404 at root
    return (
        "TruCite backend is running. "
        "Add static/index.html (preferred) or index.html in repo root.",
        200,
    )


# ------------------------------------------------------------------
# HEALTH CHECK
# ------------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "trucite-backend",
        "time_utc": utc_now_iso()
    }), 200


# ------------------------------------------------------------------
# VERIFY ENDPOINT
# ------------------------------------------------------------------
@app.post("/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body."}), 400

    normalized = normalize_text(text)
    sha = sha256_hex(normalized)
    event_id = sha[:12]
    ts = utc_now_iso()

    claims = extract_claims(text)
    claim_objs = []
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

    overall_score, overall_verdict = aggregate_score(
        per_scores, risk_tags_union
    )

    drift = compute_drift(
        sha, overall_score, overall_verdict, claim_objs, ts
    )

    return jsonify({
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
            "MVP heuristic verification. This demo flags risk via "
            "claim segmentation, uncertainty cues, citation/number "
            "patterns, and basic consistency signals. "
            "Enterprise mode adds evidence-backed checks and persistence."
        ),
        "claims": claim_objs,
        "uncertainty_map": {
            "risk_tags": sorted(list(risk_tags_union))
        },
        "drift": drift
    }), 200


# ------------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def normalize_text(s: str) -> str:
    s2 = s.lower()
    s2 = re.sub(r"\s+", " ", s2).strip()
    return s2


def extract_claims(text: str) -> List[str]:
    t = text.replace("\r\n", "\n").replace("\r", "\n")
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"[\u2022•\-]\s+", "\n", t)

    parts = re.split(r"(?<=[\.\?\!])\s+|\n+", t)
    cleaned = []
    seen = set()

    for p in parts:
        p = p.strip()
        if not p or len(p) < 12:
            continue

        p = p.strip(" \t\n-–—•")
        k = normalize_text(p)
        if k in seen:
            continue
        seen.add(k)
        cleaned.append(p)

    if not cleaned:
        cleaned = [text.strip()]

    return cleaned[:30]


def derive_signals(claim: str) -> Dict[str, Any]:
    numerics = re.findall(r"\b\d+(\.\d+)?\b", claim)
    has_percent = bool(
        re.search(r"\b\d+(\.\d+)?\s*%\b", claim)
    )
    has_citation_like = bool(
        re.search(
            r"\b(v\.|vs\.|§|U\.S\.|F\.\d+d|WL\s*\d+|No\.\s*\d+)\b",
            claim,
        )
    )
    has_url = "http://" in claim or "https://" in claim or "www." in claim

    hedges = count_matches(claim, [
        r"\bmay\b", r"\bmight\b", r"\bcould\b",
        r"\bpossible\b", r"\bpossibly\b",
        r"\bunclear\b", r"\bunknown\b",
        r"\bestimate\b", r"\bapprox\b",
        r"\bapproximately\b", r"\blikely\b",
        r"\bunlikely\b"
    ])

    absolutes = count_matches(claim, [
        r"\balways\b", r"\bnever\b",
        r"\bguarantee\b", r"\bproves?\b",
        r"\bdefinitely\b", r"\b100%\b",
        r"\bmust\b"
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
    signals = derive_signals(claim)
    base = 70
    tags = []

    if signals["absolute_count"] >= 1:
        base -= 10
        tags.append("overconfident_language")

    if signals["hedge_count"] >= 1:
        base -= 8
        tags.append("uncertainty_language")

    if signals["has_numerics"]:
        base -= 8
        tags.append("numeric_claim")

    if signals["has_citation_like"]:
        base -= 6
        tags.append("citation_sensitive")

    if signals["has_url"]:
        base += 3
        tags.append("source_link_present")

    if len(claim) < 25:
        base -= 6
        tags.append("too_short")
    if len(claim) > 240:
        base -= 6
        tags.append("too_long")

    score = int(max(0, min(100, base)))

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


def compute_drift(
    key_sha: str,
    score: int,
    verdict: str,
    claims: List[Dict[str, Any]],
    ts: str
) -> Dict[str, Any]:

    prev = DRIFT_STORE.get(key_sha)
    drift = {
        "has_prior": False,
        "prior_timestamp_utc": None,
        "score_delta": None,
        "verdict_changed": False,
        "claim_count_delta": None,
        "drift_flag": False,
        "notes": (
            "MVP in-memory drift. Enterprise mode persists "
            "histories and compares behavior over time."
        )
    }

    if prev:
        drift["has_prior"] = True
        drift["prior_timestamp_utc"] = prev.get("timestamp_utc")
        drift["score_delta"] = score - int(prev.get("score", 0))
        drift["verdict_changed"] = (
            verdict != prev.get("verdict")
        )
        drift["claim_count_delta"] = (
            len(claims) - int(prev.get("num_claims", 0))
        )

        if abs(drift["score_delta"]) >= 10 or drift["verdict_changed"]:
            drift["drift_flag"] = True

    DRIFT_STORE[key_sha] = {
        "timestamp_utc": ts,
        "score": score,
        "verdict": verdict,
        "num_claims": len(claims)
    }

    return drift


# ------------------------------------------------------------------
# RUN
# ------------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
