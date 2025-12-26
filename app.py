# app.py — TruCite backend (Render) — MVP v1.1
# Adds: Reference Grounding (stub) + cleaner response schema + health route
# Keep this file as-is and redeploy.

from flask import Flask, request, jsonify
from flask_cors import CORS
import re
from datetime import datetime, timezone

app = Flask(__name__)
CORS(app)  # allow calls from your frontend domain(s) during MVP

# -------------------------
# Helpers
# -------------------------

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def normalize_text(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip())

def extract_claims(text: str):
    """
    MVP claim extraction:
    - returns one "claim" object for now
    - later: split into multiple claims, detect modalities, etc.
    """
    t = normalize_text(text)
    if not t:
        return []
    return [{
        "id": "c1",
        "type": "factual",
        "text": t,
        "confidence_weight": 1
    }]

def heuristic_score(claim_text: str) -> int:
    """
    MVP heuristic scoring:
    - placeholder logic
    - later: grounding + evidence score + contradictions + source trust weighting
    """
    t = (claim_text or "").lower()

    # obvious "nonsense" / high-risk examples
    if "made of candy" in t or "made of of candy" in t:
        return 46

    # some simple patterns
    if "moon" in t:
        return 72
    if len(t) < 10:
        return 60

    return 70

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Plausible / Needs Verification"
    if score >= 65:
        return "Unclear / Needs Verification"
    return "Questionable / High Uncertainty"

def ground_references(claim_text: str):
    """
    Reference Grounding (stub):
    - returns a list of candidate references with fields you will later compute:
      source_type, url, title, snippet, relevance_score, support_status, retrieval_method
    - In V2: implement real retrieval (search APIs, curated corpora, RAG) + stance detection.
    """
    t = (claim_text or "").lower()
    refs = []

    # Example grounding pack for "moon" claims
    if "moon" in t:
        refs.extend([
            {
                "source_type": "encyclopedia",
                "title": "Moon (Earth's natural satellite) — general overview",
                "url": "https://en.wikipedia.org/wiki/Moon",
                "snippet": "The Moon is Earth's only natural satellite. Its composition is primarily silicate rock with a small metallic core.",
                "relevance_score": 0.82,
                "support_status": "contextual",  # not yet computed as support/contradict
                "retrieval_method": "stub_catalog"
            },
            {
                "source_type": "government",
                "title": "NASA — The Moon",
                "url": "https://science.nasa.gov/moon/",
                "snippet": "NASA science pages describing lunar formation, composition, and exploration.",
                "relevance_score": 0.88,
                "support_status": "contextual",
                "retrieval_method": "stub_catalog"
            }
        ])

    # Example grounding pack for "made of candy" (contradiction hint)
    if "candy" in t:
        refs.append({
            "source_type": "government",
            "title": "NASA — Moon composition basics (not candy)",
            "url": "https://science.nasa.gov/moon/",
            "snippet": "Lunar material is rock/regolith; claims of candy composition are not supported.",
            "relevance_score": 0.90,
            "support_status": "likely_contradict",  # heuristic hint only
            "retrieval_method": "stub_catalog"
        })

    return refs

def drift_stub():
    """
    Drift tracking (stub):
    - V2: store signature of claim + model/version + reference snapshot hash
    - compare over time for drift and notify.
    """
    return {
        "enabled": False,
        "note": "Drift tracking not enabled in MVP. Next: store claim+refs snapshot and compare over time."
    }

# -------------------------
# Routes
# -------------------------

@app.get("/")
def root():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": utc_now_iso(),
        "routes": ["/health", "/verify"]
    })

@app.get("/health")
def health():
    return jsonify({
        "ok": True,
        "service": "trucite-backend",
        "time_utc": utc_now_iso()
    })

@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    claims = extract_claims(text)
    if not claims:
        return jsonify({
            "error": "No text provided",
            "score": 0,
            "verdict": "No Input",
            "explanation": "Provide text in JSON body: { \"text\": \"...\" }",
            "claims": [],
            "references": [],
            "meta": {
                "mode": "mvp_v1_1",
                "time_utc": utc_now_iso()
            }
        }), 400

    # MVP: score based on first claim only
    claim_text = claims[0]["text"]
    score = heuristic_score(claim_text)
    verdict = verdict_from_score(score)

    # NEW: grounding layer (stub catalog)
    references = ground_references(claim_text)

    explanation = (
        "MVP mode: heuristic scoring + claim extraction + reference grounding (stub). "
        "Next: real retrieval, stance detection (support/contradict), citation ranking, and drift tracking."
    )

    return jsonify({
        "score": score,
        "verdict": verdict,
        "explanation": explanation,
        "claims": claims,
        "references": references,
        "drift": drift_stub(),
        "meta": {
            "mode": "mvp_v1_1",
            "time_utc": utc_now_iso()
        }
    })

# -------------------------
# Entry point (Render uses gunicorn app:app)
# -------------------------
if __name__ == "__main__":
    # Local run only; Render uses gunicorn app:app
    app.run(host="0.0.0.0", port=5000, debug=True)
