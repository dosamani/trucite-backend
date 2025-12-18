import os
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)  # harmless even if same-origin; keeps future flexibility

# -------------------------
# FRONTEND ROUTES (Render)
# -------------------------
@app.get("/")
def home():
    # serves /static/index.html as your homepage
    return send_from_directory(app.static_folder, "index.html")

@app.get("/<path:filename>")
def static_files(filename):
    # serves /static/style.css, /static/script.js, /static/logo.jpg, etc.
    return send_from_directory(app.static_folder, filename)

# -------------------------
# HEALTH CHECK
# -------------------------
@app.get("/health")
def health():
    return "TruCite backend ok", 200

# -------------------------
# API ROUTE
# -------------------------
@app.post("/truth-score")
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text'"}), 400

    # MVP placeholder scoring logic (replace later)
    score = 72
    verdict = "Plausible / Needs Verification"

    return jsonify({
        "truth_score": score,
        "score": score,
        "verdict": verdict,
        "details": {
            "note": "MVP placeholder scoring logic. Replace with real verification + citations later.",
            "input_chars": len(text)
        },
        "references": []
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
