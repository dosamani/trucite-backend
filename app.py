
from flask import Flask, request, jsonify
from flask_cors import CORS
import os
import re

app = Flask(__name__)
CORS(app)

@app.route("/", methods=["GET"])
def root():
    return jsonify({
        "service": "trucite-backend",
        "status": "ok",
        "routes": ["/", "/health", "/verify"],
        "message": "Root route is live. Use POST /verify with JSON: {\"text\":\"...\"}"
    })

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input",
            "explanation": "No text provided.",
            "claims": []
        })

    hits = len(re.findall(r"\b(fake|made up|nonsense|impossible|myth|false)\b", text.lower()))
    score = max(0, min(100, 100 - hits * 15))

    verdict = "Plausible / Needs Verification"
    if score < 50:
        verdict = "Questionable / High Uncertainty"
    if score < 30:
        verdict = "Likely False"

    claims = [{
        "id": "c1",
        "type": "factual",
        "text": text,
        "confidence_weight": 1
    }]

    return jsonify({
        "score": score,
        "verdict": verdict,
        "explanation": "MVP score based on heuristic mode. Reference grounding and drift tracking will be added next.",
        "claims": claims
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
