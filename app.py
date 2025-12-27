import os
import re
import uuid
import json
import hashlib
from datetime import datetime, timezone

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS


APP_NAME = "TruCite"
ENGINE_VERSION = "TruCite Claim Engine v2 (MVP)"
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

app = Flask(__name__, static_folder=STATIC_DIR, static_url_path="/static")
CORS(app, resources={r"/*": {"origins": "*"}})


# -----------------------------
# Helpers (kept simple / MVP)
# -----------------------------

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"

def clamp_int(n, lo=0, hi=100):
    try:
        n = int(round(float(n)))
    except Exception:
        n = 0
    return max(lo, min(hi, n))

def simple_claim_extract(text: str, max_claims: int = 3):
    cleaned = re.sub(r"\s+", " ", (text or "")).strip()
    if not cleaned:
        return []

    parts = re.split(r"(?<=[\.\?\!])\s+", cleaned)
    parts = [p.strip() for p in parts if p.strip()]

    claims = []
    for idx, p in enumerate(parts[:max_claims], start=1):
        claims.append({
            "id": f"c{idx}",
            "text": p,
            "type": "factual",
            "confidence_weight": 1
        })
    return claims

def simple_score(text: str) -> int:
    t = (text or "").strip().lower()
    if not t:
        return 0

    score = 72

    nonsense_markers = [
        "moon is made of", "earth is flat", "2+2=5", "candy", "cheese", "aliens built"
    ]
    for m in nonsense_markers:
        if m in t:
            score -= 18
            break

    if any(x in t for x in ["always", "never", "guaranteed", "100%"]):
        score -= 6

    if any(x in t for x in ["may", "might", "unclear", "possibly", "likely", "suggests"]):
        score += 4

    return clamp_int(score)

def build_trust_profile(score: int, claims_count: int):
    reliability = round(score / 100.0, 2)

    volatility = 0.30
    if claims_count <= 1:
        volatility += 0.15
    if score < 55:
        volatility += 0.20
    volatility = round(min(0.95, max(0.05, volatility)), 2)

    grounding_strength = round(max(0.05, reliability - 0.10), 2)
    drift_risk = round(min(0.95, max(0.05, 1.0 - reliability + 0.10)), 2)

    return {
        "reliability": reliability,
        "volatility": volatility,
        "grounding_strength": grounding_strength,
        "drift_risk": drift_risk
    }

def build_risk_summary(score: int):
    if score >= 85:
        return {"regulatory_exposure": "Low", "misinformation_risk": "Low", "model_confidence_gap": "Minimal"}
    if score >= 65:
        return {"regulatory_exposure": "Medium", "misinformation_risk": "Medium", "model_confidence_gap": "Moderate"}
    if score >= 40:
        return {"regulatory_exposure": "Medium", "misinformation_risk": "High", "model_confidence_gap": "Significant"}
    return {"regulatory_exposure": "High", "misinformation_risk": "High", "model_confidence_gap": "Severe"}

def compute_hash(payload: dict) -> str:
    """
    Deterministic SHA-256 over canonical JSON.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# -----------------------------
# Routes
# -----------------------------

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "ts_utc": utc_now_iso()})

@app.get("/")
def root():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(STATIC_DIR, "index.html")
    return jsonify({"ok": True, "message": "Static index.html not found in /static"}), 200

@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)

def score_payload(text: str):
    event_id = str(uuid.uuid4())
    score = simple_score(text)
    claims = simple_claim_extract(text, max_claims=3)
    verdict = verdict_from_score(score)
    ts = utc_now_iso()

    trust_profile = build_trust_profile(score=score, claims_count=len(claims))
    risk_summary = build_risk_summary(score=score)

    # Hash only the audit-relevant fields (no UI fluff)
    audit_payload = {
        "engine_version": ENGINE_VERSION,
        "timestamp_utc": ts,
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
        "claims": claims,
        "trust_profile": trust_profile,
        "risk_summary": risk_summary,
    }
    audit_hash = compute_hash(audit_payload)

    response = {
        "event_id": event_id,
        "claims": claims,
        "explanation": (
            "MVP mode: returning a baseline score plus extracted claims. "
            "Next steps will add reference-grounding and drift tracking."
        ),
        "score": score,
        "verdict": verdict,
        "trust_profile": trust_profile,
        "risk_summary": risk_summary,
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "timestamp_utc": ts,
            "hash": audit_hash
        }
    }
    return response

@app.post("/truth-score")
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Missing 'text'"}), 400
    return jsonify(score_payload(text))

@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Missing 'text'"}), 400
    return jsonify(score_payload(text))

@app.post("/api/score")
def api_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Missing 'text'"}), 400
    return jsonify(score_payload(text))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
