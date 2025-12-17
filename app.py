import os
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="")

# ---------- FRONTEND (served from same origin) ----------
@app.route("/", methods=["GET"])
def home():
    return send_from_directory(app.static_folder, "index.html")

@app.route("/<path:path>", methods=["GET"])
def static_proxy(path):
    # Serves /style.css, /script.js, /logo.jpg, etc.
    return send_from_directory(app.static_folder, path)

# ---------- BACKEND API ----------
@app.route("/api/score", methods=["POST"])
def score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing text"}), 400

    # Deterministic demo scoring (replace later with real verifier)
    length = len(text)
    score_val = min(100, max(5, int(length / 4)))

    if score_val >= 85:
        verdict = "Likely True / Well-Supported"
    elif score_val >= 65:
        verdict = "Plausible / Needs Verification"
    elif score_val >= 40:
        verdict = "Questionable / High Uncertainty"
    else:
        verdict = "Likely False / Misleading"

    return jsonify({
        "score": score_val,
        "verdict": verdict,
        "engine": "trucite-demo",
        "chars": length
    })

@app.route("/health", methods=["GET"])
def health():
    return "TruCite backend ok (full-stack)", 200
