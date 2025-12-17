from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# ✅ THIS LINE FIXES NEOCITIES / CORS
CORS(app, resources={r"/api/*": {"origins": "*"}})

@app.route("/", methods=["GET"])
def health():
    return "TruCite backend ok", 200

@app.route("/api/score", methods=["POST"])
def score():
    data = request.get_json(force=True)
    text = data.get("text", "")

    if not text.strip():
        return jsonify({
            "error": "No text provided"
        }), 400

    # 🔹 TEMP DEMO LOGIC (replace later)
    score = 82
    verdict = "Likely reliable"

    return jsonify({
        "score": score,
        "verdict": verdict,
        "explanation": "Demo scoring response. Backend wiring confirmed."
    })
