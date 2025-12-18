
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os

app = Flask(__name__, static_folder="static", static_url_path="")

# CORS: allow your frontend origins (Neocities) AND allow same-origin when hosted on Render
CORS(app, resources={r"/*": {"origins": "*"}})

# ---------- HEALTH ----------
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend"}), 200

# ---------- SERVE FRONTEND ----------
@app.get("/")
def home():
    # Serves static/index.html
    return send_from_directory(app.static_folder, "index.html")

# Serve any other static asset: /style.css, /script.js, /logo.jpg, etc.
@app.get("/<path:path>")
def static_proxy(path):
    full_path = os.path.join(app.static_folder, path)
    if os.path.isfile(full_path):
        return send_from_directory(app.static_folder, path)
    return jsonify({"error": "Not found", "path": path}), 404

# ---------- API ----------
# Friendly GET so browser doesn't show Method Not Allowed
@app.get("/truth-score")
def truth_score_get():
    return jsonify({
        "ok": True,
        "note": "Use POST /truth-score with JSON: {\"text\":\"...\"}"
    }), 200

@app.post("/truth-score")
def truth_score_post():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in JSON body"}), 400

    # MVP placeholder scoring (replace later with RAG, citations, drift, etc.)
    score = 78
    verdict = "Plausible / Needs Verification"

    return jsonify({
        "truth_score": score,
        "verdict": verdict,
        "explanation": "MVP placeholder score. Replace with evidence + retrieval checks + drift scoring.",
        "references": []
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
