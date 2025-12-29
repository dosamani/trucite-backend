import os
import time
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# If you have these modules in your repo, we’ll use them.
# If not, we’ll fallback gracefully.
try:
    from claim_parser import extract_claims
except Exception:
    extract_claims = None

try:
    from reference_engine import score_claim_text
except Exception:
    score_claim_text = None


app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)


# -------------------------
# Static landing page
# -------------------------
@app.get("/")
def landing():
    # Serve static/index.html
    return send_from_directory(app.static_folder, "index.html")


# (Optional but helpful) explicitly serve your static assets
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


# -------------------------
# Health check
# -------------------------
@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "trucite-backend", "ts": int(time.time())})


# -------------------------
# Verify endpoint (MUST allow POST)
# -------------------------
@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    # Handle preflight (Render + browsers)
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body"}), 400

    # Fingerprint / Event ID
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    # Claims extraction
    claims = []
    if extract_claims:
        try:
            extracted = extract_claims(text)
            # normalize to list of {"text": "..."}
            if isinstance(extracted, list):
                for c in extracted:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c["text"])})
                    elif isinstance(c, str):
                        claims.append({"text": c})
            elif isinstance(extracted, str):
                claims = [{"text": extracted}]
        except Exception:
            claims = [{"text": text}]
    else:
        claims = [{"text": text}]

    # Scoring (fallback to MVP heuristic if reference_engine not available)
    if score_claim_text:
        try:
            score, verdict, explanation = score_claim_text(text)
        except Exception:
            score, verdict, explanation = heuristic_score(text)
    else:
        score, verdict, explanation = heuristic_score(text)

    resp = {
        "verdict": verdict,
        "score": int(score),
        "event_id": event_id,
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": ts
        },
        "claims": claims,
        "explanation": explanation
    }

    return jsonify(resp), 200


def heuristic_score(text: str):
    """
    Simple MVP heuristic scoring (0-100).
    You can replace this later with evidence-backed scoring.
    """
    t = text.lower()

    # crude signals
    risky = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    score = 55

    if any(w in t for w in risky):
        score -= 15

    if any(w in t for w in hedges):
        score += 10

    # Very long / rambly text tends to be lower confidence
    if len(text) > 800:
        score -= 10

    score = max(0, min(100, score))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty/uncertainty cues "
        "and basic risk signals. Replace with evidence-backed verification in production."
    )

    return score, verdict, explanation


if __name__ == "__main__":
    # local dev only; Render uses gunicorn
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
