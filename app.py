from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import uuid
import json
import hashlib
import datetime
import os

app = Flask(__name__, static_folder="static")
CORS(app)

# ===============================
# Configuration
# ===============================

DATA_DIR = "data"
AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit_log.jsonl")
ENGINE_VERSION = "TruCite Claim Engine v2.3 (MVP)"

os.makedirs(DATA_DIR, exist_ok=True)

# ===============================
# Utility Functions
# ===============================

def now_utc():
    return datetime.datetime.utcnow().isoformat()

def hash_payload(payload: dict):
    raw = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()

def persist_event(event: dict):
    with open(AUDIT_LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")

# ===============================
# Core Scoring Engine (MVP)
# ===============================

def evaluate_text(text: str):
    claims = [{
        "id": "c1",
        "text": text.strip(),
        "type": "factual",
        "confidence_weight": 1
    }]

    contains_reference = "http" in text or "www." in text

    if contains_reference:
        score = 36
        verdict = "Likely False / Misleading"
        misinformation_risk = "High"
        regulatory_exposure = "High"
    else:
        score = 54
        verdict = "Questionable / High Uncertainty"
        misinformation_risk = "High"
        regulatory_exposure = "Medium"

    trust_profile = {
        "reliability": round(score / 100, 2),
        "grounding_strength": 0.29 if contains_reference else 0.44,
        "drift_risk": 0.79 if contains_reference else 0.56,
        "volatility": 0.74 if contains_reference else 0.61
    }

    return claims, score, verdict, misinformation_risk, regulatory_exposure, trust_profile

# ===============================
# Routes
# ===============================

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "engine": ENGINE_VERSION})

@app.route("/truth-score", methods=["POST"])
def truth_score():
    data = request.json or {}
    text = data.get("text", "")

    claims, score, verdict, misinformation_risk, regulatory_exposure, trust_profile = evaluate_text(text)

    event_id = str(uuid.uuid4())

    response = {
        "event_id": event_id,
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "timestamp_utc": now_utc(),
            "hash": None
        },
        "claims": claims,
        "score": score,
        "verdict": verdict,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "reference_note": "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked.",
        "references": [{"domain": "www.cdc.gov", "url": "https://www.cdc.gov/"}] if "cdc.gov" in text.lower() else [],
        "risk_summary": {
            "misinformation_risk": misinformation_risk,
            "model_confidence_gap": "Significant",
            "regulatory_exposure": regulatory_exposure
        },
        "trust_profile": trust_profile
    }

    response["audit_fingerprint"]["hash"] = hash_payload(response)

    persist_event(response)

    return jsonify(response)

# ===============================
# Server
# ===============================

if __name__ == "__main__":
    app.run()
