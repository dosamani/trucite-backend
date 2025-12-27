import os
import re
import json
import uuid
import hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")

# -----------------------------
# Config
# -----------------------------
ENGINE_VERSION = "TruCite Claim Engine v2.2 (MVP)"

# Hard-block Wikipedia in MVP reference grounding
BLOCKED_REFERENCE_DOMAINS = {
    "wikipedia.org",
    "www.wikipedia.org",
    "m.wikipedia.org",
}

# Allowlist for “trusted” sources (you can expand later)
TRUSTED_REFERENCE_ALLOWLIST = {
    # Examples (you can edit later)
    "cdc.gov",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "who.int",
    "fda.gov",
    "sec.gov",
    "justice.gov",
    "courts.gov",
    "europa.eu",
    "oecd.org",
}

DEFAULT_REFERENCE_MODE = "off"  # off | allowlist


# -----------------------------
# Helpers
# -----------------------------
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def extract_claims(text: str):
    """
    MVP claim extraction:
    - Treat the entire input as one factual claim if short.
    - If longer, split into sentence-like chunks and treat each as a claim.
    """
    cleaned = " ".join((text or "").strip().split())
    if not cleaned:
        return []

    # Split on sentence boundaries (simple MVP heuristic)
    parts = re.split(r"(?<=[\.\!\?])\s+", cleaned)
    parts = [p.strip() for p in parts if p and p.strip()]

    claims = []
    if len(parts) <= 1:
        claims.append({
            "id": "c1",
            "type": "factual",
            "confidence_weight": 1,
            "text": cleaned
        })
        return claims

    # Cap to avoid huge payloads in MVP
    parts = parts[:8]
    for i, p in enumerate(parts, start=1):
        claims.append({
            "id": f"c{i}",
            "type": "factual",
            "confidence_weight": 1,
            "text": p
        })
    return claims


def simple_risk_and_score(text: str):
    """
    MVP scoring heuristic (non-authoritative):
    - Penalize obvious absurdities (km distance to moon, “made of candy/cheese”, etc.)
    - Penalize numeric claims without context
    """
    t = (text or "").lower()

    score = 72  # baseline default
    flags = []

    absurd_markers = [
        "made of candy", "made of cheese", "moon is cheese", "moon is candy",
        "1km from earth", "1 km from earth", "one km from earth"
    ]
    for m in absurd_markers:
        if m in t:
            score -= 18
            flags.append(f"absurd_marker:{m}")

    # Numeric claims heuristic
    if re.search(r"\b\d+(\.\d+)?\b", t):
        score -= 6
        flags.append("contains_numerics")

    # Overconfident phrasing
    if any(x in t for x in ["definitely", "guaranteed", "always", "never"]):
        score -= 4
        flags.append("overconfident_language")

    # Clamp
    score = max(0, min(100, score))

    # Risk summary mapping
    if score >= 85:
        verdict = "Likely True / Well-Supported"
        misinfo = "Low"
        exposure = "Low"
    elif score >= 65:
        verdict = "Plausible / Needs Verification"
        misinfo = "Medium"
        exposure = "Medium"
    elif score >= 40:
        verdict = "Questionable / High Uncertainty"
        misinfo = "High"
        exposure = "Medium"
    else:
        verdict = "Likely False / Misleading"
        misinfo = "High"
        exposure = "High"

    return score, verdict, {
        "misinformation_risk": misinfo,
        "model_confidence_gap": "Significant" if score < 70 else "Moderate",
        "regulatory_exposure": exposure,
        "flags": flags
    }


def build_trust_profile(score: int):
    """
    MVP trust profile derived from score.
    """
    reliability = round(score / 100.0, 2)
    volatility = round(max(0.2, 1.0 - reliability) * 0.7 + 0.2, 2)
    drift_risk = round(min(0.95, 1.0 - reliability * 0.85), 2)
    grounding_strength = round(min(0.95, reliability * 0.8), 2)

    return {
        "reliability": reliability,
        "volatility": volatility,
        "drift_risk": drift_risk,
        "grounding_strength": grounding_strength
    }


def references_from_text(text: str, mode: str):
    """
    MVP reference extractor:
    - Looks for URLs in the text.
    - If mode is allowlist: keep only allowlisted domains.
    - Always block Wikipedia.
    NOTE: This does NOT fetch anything. It only surfaces candidate references.
    """
    if mode not in ("off", "allowlist"):
        mode = DEFAULT_REFERENCE_MODE

    if mode == "off":
        return [], "Reference grounding not enabled in MVP. Wikipedia blocked as a reference source."

    urls = re.findall(r"https?://[^\s\)\]]+", text or "")
    refs = []
    for u in urls:
        domain = re.sub(r"^https?://", "", u).split("/")[0].lower()

        # strip port
        domain = domain.split(":")[0]

        # block wikipedia
        if domain in BLOCKED_REFERENCE_DOMAINS or domain.endswith(".wikipedia.org"):
            continue

        # allowlist filter
        if not any(domain == d or domain.endswith("." + d) for d in TRUSTED_REFERENCE_ALLOWLIST):
            continue

        refs.append({"url": u, "domain": domain})

    note = "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked."
    return refs, note


# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "engine_version": ENGINE_VERSION})


@app.post("/truth-score")
def truth_score():
    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in JSON body."}), 400

    reference_mode = (payload.get("reference_mode") or DEFAULT_REFERENCE_MODE).strip().lower()
    claims = extract_claims(text)

    score, verdict, risk_summary = simple_risk_and_score(text)
    trust_profile = build_trust_profile(score)

    event_id = str(uuid.uuid4())
    fp_hash = sha256_hex(f"{ENGINE_VERSION}|{text}|{event_id}|{utc_now_iso()}")

    refs, ref_note = references_from_text(text, reference_mode)

    resp = {
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
        "claims": claims,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "references": refs,
        "reference_note": ref_note,
        "risk_summary": {
            "misinformation_risk": risk_summary["misinformation_risk"],
            "model_confidence_gap": risk_summary["model_confidence_gap"],
            "regulatory_exposure": risk_summary["regulatory_exposure"]
        },
        "trust_profile": trust_profile,
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "hash": fp_hash,
            "timestamp_utc": utc_now_iso()
        }
    }
    return jsonify(resp)


@app.get("/")
def serve_index():
    # Serve the landing page from /static/index.html
    return send_from_directory(app.static_folder, "index.html")


# -----------------------------
# Entrypoint
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
