import os
import re
import json
import uuid
import hashlib
from datetime import datetime, timezone
from flask import Flask, request, jsonify, send_from_directory, make_response

# ===============================
# TruCite Backend (MVP v2.2)
# - /health (GET)
# - /truth-score (POST)
# - serves /static/index.html at /
# ===============================

APP_VERSION = "TruCite Claim Engine v2.2 (MVP)"
BLOCK_WIKIPEDIA_AS_REFERENCE = True

app = Flask(__name__, static_folder="static", static_url_path="/static")


# -------------------------------
# Helpers
# -------------------------------
def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def clamp(n, lo=0.0, hi=1.0):
    return max(lo, min(hi, n))


def simple_claim_extract(text: str):
    """
    MVP claim extraction:
    - Treat the input as one factual claim (for now).
    Later: split into multiple claims with sentence segmentation + entity/number extraction.
    """
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        return []
    return [{
        "id": "c1",
        "type": "factual",
        "text": cleaned,
        "confidence_weight": 1
    }]


def heuristic_score(text: str):
    """
    MVP heuristic scoring. Purpose: demonstrate workflow & plumbing, not truth.
    We score down when the text contains obvious impossibilities / nonsense patterns.
    """
    t = text.lower().strip()

    # Hard red flags / obvious impossibilities
    red_flags = [
        "moon is made of", "moon is 1km", "earth is flat",
        "candy", "cheese", "1km from earth"
    ]

    # Uncertainty language slightly reduces score (needs verification)
    uncertainty_terms = [
        "might", "maybe", "possibly", "uncertain", "not sure",
        "could be", "suggests", "appears"
    ]

    # Overconfidence / absolute claims in risky contexts
    absolute_terms = ["guaranteed", "always", "never", "proven", "100%"]

    score = 0.72  # baseline

    if any(rf in t for rf in red_flags):
        score -= 0.18

    score -= 0.04 * sum(1 for w in uncertainty_terms if w in t)
    score -= 0.03 * sum(1 for w in absolute_terms if w in t)

    # numeric density can be a risk signal if ungrounded (very rough)
    nums = re.findall(r"\b\d+(\.\d+)?\b", t)
    if len(nums) >= 2:
        score -= 0.04

    score = clamp(score, 0.10, 0.92)
    return round(score * 100)


def verdict_from_score(score: int):
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"


def risk_summary_from_score(score: int):
    if score >= 85:
        return {
            "misinformation_risk": "Low",
            "model_confidence_gap": "Minimal",
            "regulatory_exposure": "Low"
        }
    if score >= 65:
        return {
            "misinformation_risk": "Medium",
            "model_confidence_gap": "Moderate",
            "regulatory_exposure": "Low"
        }
    if score >= 40:
        return {
            "misinformation_risk": "High",
            "model_confidence_gap": "Significant",
            "regulatory_exposure": "Medium"
        }
    return {
        "misinformation_risk": "Severe",
        "model_confidence_gap": "Critical",
        "regulatory_exposure": "High"
    }


def trust_profile_from_score(score: int):
    # Simple mapping to keep output consistent for demo
    r = score / 100.0
    return {
        "reliability": round(r, 2),
        "grounding_strength": round(clamp(r - 0.10), 2),
        "drift_risk": round(clamp(1.0 - r + 0.10), 2),
        "volatility": round(clamp(1.0 - r + 0.15), 2)
    }


def corsify(resp):
    # Keep permissive for MVP demo/testing.
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


# -------------------------------
# Routes
# -------------------------------
@app.route("/", methods=["GET"])
def serve_index():
    # Serve landing page from /static/index.html
    return send_from_directory(app.static_folder, "index.html")


@app.route("/health", methods=["GET"])
def health():
    resp = jsonify({"ok": True, "service": "trucite-backend", "version": APP_VERSION})
    return corsify(resp)


@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    if request.method == "OPTIONS":
        return corsify(make_response("", 204))

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        resp = jsonify({"error": "Missing 'text' in JSON body."})
        return corsify(resp), 400

    event_id = str(uuid.uuid4())
    score = heuristic_score(text)
    verdict = verdict_from_score(score)

    claims = simple_claim_extract(text)

    # Deterministic-ish hash for audit fingerprinting
    # (hash includes event_id so every request is unique, but still traceable)
    audit_hash = sha256_hex(f"{APP_VERSION}|{event_id}|{text}")

    # References placeholder (no Wikipedia)
    references = []
    if BLOCK_WIKIPEDIA_AS_REFERENCE:
        # keep explicit note for later reference-grounding step
        reference_note = "Reference grounding not enabled in MVP. Wikipedia blocked as a reference source."
    else:
        reference_note = "Reference grounding not enabled in MVP."

    response = {
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
        "explanation": "MVP mode: returning a baseline score plus extracted claims. Next steps will add reference-grounding and drift tracking.",
        "claims": claims,
        "references": references,
        "reference_note": reference_note,
        "risk_summary": risk_summary_from_score(score),
        "trust_profile": trust_profile_from_score(score),
        "audit_fingerprint": {
            "engine_version": APP_VERSION,
            "hash": audit_hash,
            "timestamp_utc": utc_now_iso()
        }
    }

    resp = jsonify(response)
    return corsify(resp)


# -------------------------------
# Static file support (optional)
# -------------------------------
@app.route("/static/<path:filename>", methods=["GET"])
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
