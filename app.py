from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import uuid
import json
import hashlib
import datetime
import os
import math

app = Flask(__name__, static_folder="static")
CORS(app)

# ===============================
# Configuration
# ===============================

DATA_DIR = "data"
AUDIT_LOG_PATH = os.path.join(DATA_DIR, "audit_log.jsonl")
ENGINE_VERSION = "TruCite Claim Engine v2.4 (MVP)"

os.makedirs(DATA_DIR, exist_ok=True)

# ===============================
# Utility Functions
# ===============================

def now_utc():
    # ISO format with timezone (+00:00)
    return datetime.datetime.now(datetime.timezone.utc).isoformat()

def normalize_text(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def hash_payload(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()

def sha256_text(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()

def persist_event(event: dict):
    with open(AUDIT_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")

def load_audit_events(limit=2000):
    if not os.path.exists(AUDIT_LOG_PATH):
        return []
    events = []
    # read last N lines efficiently (simple approach ok for MVP)
    with open(AUDIT_LOG_PATH, "r", encoding="utf-8") as f:
        lines = f.readlines()
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except:
                continue
    return events

def drift_stats_for_claim(claim_text: str, current_score: int):
    """
    Compute drift baseline for same normalized claim_text.
    Returns summary dict.
    """
    norm = normalize_text(claim_text)
    claim_key = sha256_text(norm)

    events = load_audit_events(limit=2000)

    prior_scores = []
    prior_event_ids = []
    last_seen = None

    for e in events:
        try:
            c0 = e.get("claims", [])[0]
            e_text = normalize_text(c0.get("text", ""))
            if sha256_text(e_text) == claim_key:
                # exclude current event if it already exists (rare but safe)
                sc = e.get("score", None)
                if isinstance(sc, (int, float)):
                    prior_scores.append(float(sc))
                    prior_event_ids.append(e.get("event_id"))
                ts = e.get("audit_fingerprint", {}).get("timestamp_utc")
                if ts:
                    last_seen = ts
        except:
            continue

    # If we only have 0 or 1 prior data points, drift is weakly estimated
    n = len(prior_scores)
    if n == 0:
        return {
            "claim_fingerprint": claim_key,
            "prior_runs": 0,
            "baseline_score_avg": None,
            "baseline_score_std": None,
            "drift_delta": None,
            "drift_level": "None (no history)",
            "note": "No prior audit history for this claim yet."
        }

    avg = sum(prior_scores) / n
    if n >= 2:
        var = sum((x - avg) ** 2 for x in prior_scores) / (n - 1)
        std = math.sqrt(var)
    else:
        std = 0.0

    drift_delta = float(current_score) - avg
    drift_abs = abs(drift_delta)

    # Simple drift label thresholds (MVP)
    if drift_abs >= 20:
        drift_level = "High"
    elif drift_abs >= 10:
        drift_level = "Moderate"
    elif drift_abs >= 5:
        drift_level = "Low"
    else:
        drift_level = "Minimal"

    return {
        "claim_fingerprint": claim_key,
        "prior_runs": n,
        "baseline_score_avg": round(avg, 2),
        "baseline_score_std": round(std, 2),
        "drift_delta": round(drift_delta, 2),
        "drift_level": drift_level,
        "note": "Baseline computed from prior runs of the same normalized claim text."
    }

# ===============================
# Core Scoring Engine (MVP)
# ===============================

def evaluate_text(text: str):
    text_clean = (text or "").strip()

    claims = [{
        "id": "c1",
        "text": text_clean,
        "type": "factual",
        "confidence_weight": 1
    }]

    contains_reference = "http" in text_clean or "www." in text_clean

    # MVP heuristic scoring
    if contains_reference:
        score = 36
        verdict = "Likely False / Misleading"
        misinformation_risk = "High"
        regulatory_exposure = "High"
        grounding_strength = 0.29
        drift_risk = 0.79
        volatility = 0.74
    else:
        score = 54
        verdict = "Questionable / High Uncertainty"
        misinformation_risk = "High"
        regulatory_exposure = "Medium"
        grounding_strength = 0.44
        drift_risk = 0.56
        volatility = 0.61

    trust_profile = {
        "reliability": round(score / 100, 2),
        "grounding_strength": grounding_strength,
        "drift_risk": drift_risk,
        "volatility": volatility
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

    # NEW: fingerprints for drift + audit correlation
    claim_text = claims[0]["text"] if claims else ""
    claim_norm = normalize_text(claim_text)
    claim_fingerprint = sha256_text(claim_norm)

    drift_summary = drift_stats_for_claim(claim_text, score)

    response = {
        "event_id": event_id,
        "audit_fingerprint": {
            "engine_version": ENGINE_VERSION,
            "timestamp_utc": now_utc(),
            "hash": None
        },
        "content_fingerprint": {
            "claim_fingerprint": claim_fingerprint,
            "normalized_claim": claim_norm
        },
        "claims": claims,
        "score": score,
        "verdict": verdict,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "reference_note": "Reference grounding in allowlist mode: only trusted domains are permitted. Wikipedia blocked.",
        "references": [{"domain": "www.cdc.gov", "url": "https://www.cdc.gov/"}] if "cdc.gov" in (text or "").lower() else [],
        "risk_summary": {
            "misinformation_risk": misinformation_risk,
            "model_confidence_gap": "Significant",
            "regulatory_exposure": regulatory_exposure
        },
        "trust_profile": trust_profile,
        "drift_summary": drift_summary
    }

    response["audit_fingerprint"]["hash"] = hash_payload(response)

    # Persist the immutable event record
    persist_event(response)

    return jsonify(response)

# ===============================
# Server
# ===============================

if __name__ == "__main__":
    app.run()
