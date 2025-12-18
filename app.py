
import os
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# ✅ EXACT Neocities origin
CORS(
    app,
    resources={
        r"/truth-score": {
            "origins": ["https://trucite-sandbox.neocities.org"]
        }
    },
    methods=["POST", "OPTIONS"],
    allow_headers=["Content-Type"]
)

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    # ✅ Explicit OPTIONS handling (this is what was missing before)
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing text"}), 400

    return jsonify({
        "mode": "demo",
        "score": 82,
        "verdict": "Likely reliable"
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
