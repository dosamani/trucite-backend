from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/*": {"origins": "*"}})

# ----------------------------
# LANDING PAGE (Render-hosted)
# ----------------------------
@app.get("/")
def home():
    # serve /static/index.html as the homepage
    return send_from_directory(app.static_folder, "index.html")

# Serve any static file: /style.css, /script.js, /logo.jpg, etc.
@app.get("/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ----------------------------
# HEALTH CHECK
# ----------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True})

# ----------------------------
# TRUTH SCORE API (POST)
# ----------------------------
@app.post("/truth-score")
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "truth_score": 0,
            "verdict": "No input",
            "explanation": "No text provided."
        }), 400

    # Simple MVP logic for now (replace later with RAG / drift / references)
    lowered = text.lower()
    if "moon" in lowered and "cheese" in lowered:
        score = 10
        verdict = "Likely False / Misleading"
        explanation = "The claim 'the moon is made of cheese' is a well-known false statement."
        references = [
            {"title": "NASA – The Moon", "url": "https://moon.nasa.gov/"}
        ]
    else:
        score = 70
        verdict = "Plausible / Needs Verification"
        explanation = "This is an MVP heuristic score. Add evidence/RAG next."
        references = [
            {"title": "NASA", "url": "https://www.nasa.gov/"}
        ]

    return jsonify({
        "truth_score": score,
        "verdict": verdict,
        "explanation": explanation,
        "references": references
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
