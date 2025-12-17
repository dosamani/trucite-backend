import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Serve files out of /static
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)  # allow Neocities + other origins during demo

@app.get("/health")
def health():
    return "TruCite backend OK", 200

# Serve the landing page at root
@app.get("/")
def serve_index():
    return send_from_directory(app.static_folder, "index.html")

# Also serve any other static assets (style.css, script.js, logo.jpg, etc)
@app.get("/<path:filename>")
def serve_static(filename):
    return send_from_directory(app.static_folder, filename)

@app.post("/api/score")
def score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input",
            "details": "Empty input text."
        }), 400

    # Simple demo scoring logic (replace later with real scoring)
    lower = text.lower()
    if "moon is made of cheese" in lower:
        score = 5
        verdict = "Likely False / Misleading"
    elif "384,400 km" in lower or "384400 km" in lower:
        score = 90
        verdict = "Likely True / Well-Supported"
    else:
        score = 70
        verdict = "Plausible / Needs Verification"

    return jsonify({
        "score": score,
        "verdict": verdict,
        "details": "Demo scoring logic (placeholder).",
        "references": []
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
