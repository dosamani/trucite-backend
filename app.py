import os
import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# Step 2 import: claim parser
from claim_parser import parse_claims

app = Flask(__name__, static_folder="static", static_url_path="/static")

# CORS: allow frontend to call backend
CORS(app, resources={r"/*": {"origins": "*"}})

# -----------------------------
# ROUTES
# -----------------------------

# Serve landing page from Render (static/index.html)
@app.route("/", methods=["GET"])
def home():
    return send_from_directory("static", "index.html")

# Optional: serve favicon if you add it later
@app.route("/favicon.ico", methods=["GET"])
def favicon():
    # Only works if you add static/favicon.ico
    return send_from_directory("static", "favicon.ico")

# Health check
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True})

# Main scoring endpoint (POST only)
@app.route("/truth-score", methods=["POST"])
def truth_score():
    try:
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

        if not text:
            return jsonify({
                "score": 0,
                "verdict": "No input",
                "explanation": "No text provided. Send JSON: { \"text\": \"...\" }",
                "claims": []
            }), 400

        # Step 2: parse claims
        claims = parse_claims(text)

        # -----------------------------
        # MVP scoring logic (simple + stable)
        # Replace later with real scoring / RAG / drift
        # -----------------------------
        score = 72  # temporary baseline so UI works consistently
        verdict = "Plausible / Needs Verification"

        explanation = (
            "MVP mode: returning a baseline score plus extracted claims. "
            "Next steps will add reference-grounding and drift tracking."
        )

        return jsonify({
            "score": score,
            "verdict": verdict,
            "explanation": explanation,
            "claims": claims
        })

    except Exception as e:
        return jsonify({
            "score": 0,
            "verdict": "Server error",
            "explanation": f"Exception: {str(e)}",
            "claims": []
        }), 500


# -----------------------------
# LOCAL DEV
# -----------------------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
