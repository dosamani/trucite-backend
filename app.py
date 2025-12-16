from flask import Flask, request, jsonify
from flask_cors import CORS
import os

app = Flask(__name__)

# Allow browser calls from your Neocities site (and anywhere for now)
# You can lock this down later by replacing "*" with "https://trucite-sandbox.neocities.org"
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.get("/")
def home():
    return "TruCite backend ok"

@app.get("/ping")
def ping():
    return "ok"

@app.post("/api/score")
def score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input provided",
            "details": "Paste some AI output first."
        }), 400

    # ---- DEMO scoring (replace later with real TruCite logic) ----
    # Simple heuristic: longer text => slightly higher score, capped
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
            "note": "This is a placeholder scoring function. Replace with real TruCite scoring engine."
        },
        "references": []
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
