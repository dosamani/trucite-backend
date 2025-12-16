from flask import Flask, request, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)

# MOST permissive CORS for debugging Neocities → Render.
# You can lock down origins later.
CORS(app, supports_credentials=True, resources={r"/*": {"origins": "*"}})

@app.get("/")
def home():
    return "TruCite backend ok"

@app.get("/ping")
def ping():
    return "ok"

@app.route("/api/score", methods=["POST", "OPTIONS"])
def score():
    # Handle preflight explicitly (some browsers require this cleanly)
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input provided",
            "details": "Paste some AI output first."
        }), 400

    # Demo scoring (replace later)
    length = len(text)
    score = min(95, max(5, int(length / 20)))

    verdict = (
        "Likely reliable (demo)"
        if score >= 75 else
        "Needs review (demo)"
        if score >= 45 else
        "High risk / likely incorrect (demo)"
    )

    return jsonify({
        "score": score,
        "verdict": verdict,
        "details": {
            "mode": "demo-live-backend",
            "text_length": length,
            "note": "Placeholder scoring. Replace with TruCite scoring engine."
        },
        "references": []
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
