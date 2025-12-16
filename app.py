
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# 🔑 THIS IS THE FIX
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=False
)

@app.route("/", methods=["GET"])
def health():
    return "TruCite backend ok", 200

@app.route("/api/score", methods=["POST", "OPTIONS"])
def score():
    if request.method == "OPTIONS":
        return "", 200

    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    length = len(text)

    # simple deterministic scoring (demo)
    score = min(100, max(5, int(length / 4)))
    verdict = (
        "Likely True / Well-Supported" if score >= 85 else
        "Plausible / Needs Verification" if score >= 65 else
        "Questionable / High Uncertainty"
    )

    return jsonify({
        "score": score,
        "verdict": verdict,
        "engine": "trucite-demo",
        "chars": length
    })
