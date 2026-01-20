import os
import re
import json
import hashlib
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple, Optional

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")

# =========================
# Helpers
# =========================

def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def count_matches(text: str, patterns: List[str]) -> int:
    t = text.lower()
    c = 0
    for p in patterns:
        if re.search(p, t, flags=re.IGNORECASE):
            c += 1
    return c

def split_into_claims(text: str) -> List[str]:
    """
    MVP claim segmentation:
    - Split on sentence boundaries, newlines, semicolons
    - Keep short claims, but remove empties
    """
    raw = re.split(r"[\n\r;]+", text.strip())
    claims: List[str] = []
    for block in raw:
        block = block.strip()
        if not block:
            continue
        # further split sentences
        parts = re.split(r"(?<=[.!?])\s+", block)
        for p in parts:
            p = p.strip()
            if p:
                claims.append(p)
    # cap for safety
    return claims[:12]

def derive_signals(claim: str) -> Dict[str, Any]:
    """
    Signals derived from claim text.
    Key fixes:
    - Detect numerics even when attached to units (e.g., 1km, 10mg)
    """
    c = claim.strip()

    # Standalone numbers like "10" or "3.14"
    numerics = re.findall(r"\b\d+(?:\.\d+)?\b", c)

    # Numbers with unit suffix/prefix: "1km", "10 mg", "5lbs", "2x"
    unit_numerics = re.findall(r"\b\d+(?:\.\d+)?\s*[a-zA-Z]{1,6}\b", c)

    any_numeric = (len(numerics) > 0) or (len(unit_numerics) > 0)

    has_percent = bool(re.search(r"\b\d+(\.\d+)?\s*%\b", c))
    has_url = ("http://" in c.lower()) or ("https://" in c.lower()) or ("www." in c.lower())

    # Citation-ish markers (very lightweight MVP)
    has_citation_like = bool(
        re.search(r"\b(v\.|vs\.|§|u\.s\.|f\.\d+d|wl\s*\d+|no\.\s*\d+)\b", c, flags=re.IGNORECASE)
        or re.search(r"\[(\d+)\]", c)
        or re.search(r"\((19|20)\d{2}\)", c)  # year in parentheses
    )

    hedge_count = len(re.findall(
        r"\b(may|might|could|possible|possibly|unclear|unknown|estimate|approx|approximately|likely|unlikely)\b",
        c,
        flags=re.IGNORECASE
    ))

    absolute_count = len(re.findall(
        r"\b(always|never|guarantee|guarantees|guaranteed|prove|proves|definitely|must)\b",
        c,
        flags=re.IGNORECASE
    ))

    # Simple absurdity patterns (MVP); you can expand later
    absurd_term_hits = 0
    absurd_patterns = [
        r"\bmade up of\b.*\bcandy\b",
        r"\bmoon\b.*\bcandy\b",
        r"\bteleport\b",
        r"\bmagic\b",
        r"\bflat earth\b",
        r"\bunicorn\b",
    ]
    for ap in absurd_patterns:
        if re.search(ap, c, flags=re.IGNORECASE):
            absurd_term_hits += 1

    return {
        "has_numerics": any_numeric,
        "numeric_count": len(numerics) + len(unit_numerics),
        "has_percent": has_percent,
        "has_url": has_url,
        "has_citation_like": has_citation_like,
        "hedge_count": hedge_count,
        "absolute_count": absolute_count,
        "absurd_term_hits": absurd_term_hits
    }

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Likely reliable"
    if score >= 65:
        return "Unclear / needs verification"
    if score >= 45:
        return "Risky / verify before use"
    return "High risk / likely unreliable"

def score_claim(claim: str) -> Tuple[int, str, List[str], Dict[str, Any]]:
    """
    MVP heuristic scoring:
    Start at 85 and subtract risk penalties.
    """
    base = 85
    tags: List[str] = []

    signals = derive_signals(claim)
    text_lower = claim.lower()

    # Numeric claims are riskier, especially without any citation-like hint or URL
    if signals["has_numerics"]:
        base -= 12
        tags.append("numeric_claim")

    if signals["has_numerics"] and (not signals["has_url"]) and (not signals["has_citation_like"]):
        base -= 10
        tags.append("numeric_without_support")

    # Citation/URL patterns: in MVP, we don't confirm truth, but presence affects confidence
    if signals["has_url"]:
        base += 2
        tags.append("has_url")

    if signals["has_citation_like"]:
        base += 2
        tags.append("citation_like")

    # Absolutes without evidence can be risky
    if signals["absolute_count"] >= 1 and (not signals["has_url"]) and (not signals["has_citation_like"]):
        base -= 10
        tags.append("absolute_language")

    # Hedge language reduces confidence (not always bad; but shows uncertainty)
    if signals["hedge_count"] >= 2:
        base -= 6
        tags.append("high_uncertainty_language")

    # Absurdity / nonsense patterns should tank score
    if signals.get("absurd_term_hits", 0) >= 1:
        base -= 35
        tags.append("nonsense_pattern")

    # "Fabricated citations" cues (very lightweight)
    if re.search(r"\b(as cited in|according to a study but no details)\b", text_lower):
        base -= 10
        tags.append("vague_citation")

    # Clamp to [0, 100]
    base = max(0, min(100, int(round(base))))

    verdict = verdict_from_score(base)
    return base, verdict, tags, signals

# =========================
# Drift (MVP stub, in-memory)
# =========================
LAST_RUNS: Dict[str, Dict[str, Any]] = {}

def drift_check(fingerprint: str, score: int, num_claims: int) -> Dict[str, Any]:
    """
    MVP in-memory drift:
    - If we've seen this fingerprint before, compare score and claim count
    Enterprise mode would persist by workflow / model / tenant.
    """
    prior = LAST_RUNS.get(fingerprint)
    if not prior:
        LAST_RUNS[fingerprint] = {"score": score, "num_claims": num_claims, "ts": now_utc_iso()}
        return {
            "has_prior": False,
            "drift_flag": False,
            "score_delta": None,
            "claim_count_delta": None,
            "verdict_changed": False,
            "prior_timestamp_utc": None,
            "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time."
        }

    score_delta = score - prior["score"]
    claim_delta = num_claims - prior["num_claims"]

    # Drift heuristic: large score swing
    drift_flag = abs(score_delta) >= 20

    # Update memory
    LAST_RUNS[fingerprint] = {"score": score, "num_claims": num_claims, "ts": now_utc_iso()}

    return {
        "has_prior": True,
        "drift_flag": drift_flag,
        "score_delta": score_delta,
        "claim_count_delta": claim_delta,
        "verdict_changed": False,  # You can compute based on verdict buckets later
        "prior_timestamp_utc": prior["ts"],
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time."
    }

# =========================
# Routes
# =========================

@app.get("/")
def root():
    """
    Serve landing page if you keep index.html inside /static.
    """
    index_path = os.path.join(app.static_folder, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(app.static_folder, "index.html")
    return "TruCite backend is running.", 200

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "timestamp_utc": now_utc_iso()}), 200

@app.post("/score")
def score():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # Claim segmentation
    claims = split_into_claims(text)

    claim_results: List[Dict[str, Any]] = []
    scores: List[int] = []

    for c in claims:
        s, v, tags, sigs = score_claim(c)
        scores.append(s)
        claim_results.append({
            "text": c,
            "score": s,
            "verdict": v,
            "risk_tags": tags,
            "signals": sigs
        })

    # Composite score: average (MVP)
    overall = int(round(sum(scores) / max(1, len(scores))))
    overall_verdict = verdict_from_score(overall)

    # Audit fingerprint (stable for same input)
    fp = sha256_hex(text)
    event_id = fp[:12]

    drift = drift_check(fp, overall, len(claims))

    response = {
        "audit_fingerprint": {
            "sha256": fp,
            "timestamp_utc": now_utc_iso()
        },
        "event_id": event_id,
        "input": {
            "length_chars": len(text),
            "num_claims": len(claims)
        },
        "score": overall,
        "verdict": overall_verdict,
        "claims": claim_results,
        "drift": drift,
        "uncertainty_map": {},  # placeholder for future
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, uncertainty cues, "
            "citation/number patterns, and basic consistency signals. Enterprise mode adds evidence-backed "
            "checks and persistence."
        )
    }

    return jsonify(response), 200

# =========================
# Local dev entrypoint
# =========================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
