from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os

# Serve /static/* and allow serving index.html from repo's static folder
app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

@app.get("/")
def home():
    return send_from_directory("static", "index.html")

@app.get("/health")
def health():
    return "TruCite backend ok", 200

@app.post("/api/score")
def score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # Minimal deterministic demo scoring (no external deps)
    if not text:
        return jsonify({"score": 0, "verdict": "No input"}), 400

    score = 82 if len(text) > 20 else 35
    verdict = "Likely reliable" if score >= 70 else "Questionable"

    return jsonify({
        "score": score,
        "verdict": verdict,
        "mode": "demo"
    })

# IMPORTANT for Render: do NOT app.run() for production.
# gunicorn will import app:app
if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
