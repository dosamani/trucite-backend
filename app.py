from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Serve /style.css, /script.js, /logo.jpg directly from ./static
app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app)

# Landing page at /
@app.route("/", methods=["GET"])
def serve_index():
    return send_from_directory(app.static_folder, "index.html")

# Health check
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "version": "mvp-rag-v1.1"})

# API
@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # MVP placeholder logic
    score = 78
    verdict = "Plausible / Needs Verification"

    return jsonify({
        "mode": "mvp-rag-v1.1",
        "truth_score": score,
        "verdict": verdict,
        "references": [],
        "explanation": "MVP placeholder score. Replace with real evidence pipeline."
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
