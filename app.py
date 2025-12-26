from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import re

app = Flask(__name__, static_folder="static")
CORS(app)

# -------------------------
# Homepage route (THIS fixes Render 404)
# -------------------------
@app.route("/")
def serve_home():
    return send_from_directory(app.static_folder, "index.html")


# -------------------------
# Health check
# -------------------------
@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# -------------------------
# Verification endpoint
# -------------------------
@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
    text = data.get("text", "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input",
            "explanation": "No text provided.",
            "claims": []
        })

    # Very simple MVP heuristic scoring (your current behavior preserved)
    score = min(100, max(0, 100 - len(re.findall(r"\\b(fake|made up|nonsense|impossible|myth|false)\\b", text.lower())) * 15))

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


# -------------------------
# Static file support
# -------------------------
@app.route("/<path:path>")
def static_proxy(path):
    return send_from_directory(app.static_folder, path)


# -------------------------
# Start server
# -------------------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
