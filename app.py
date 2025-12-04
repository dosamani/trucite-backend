from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# Easiest fix: allow all origins for this demo
CORS(app)   # <-- this removes strict origin issues


@app.route("/")
def health():
    return "TruCite backend OK", 200


@app.route("/truth-score", methods=["POST"])
def truth_score():
    # Read JSON from frontend
    data = request.get_json(silent=True) or {}
    text = data.get("text", "") or ""
    length = len(text)

    # Very simple demo scoring
    base = 50
    score = max(0, min(100, base + min(length // 20, 30)))

    if score >= 85:
        verdict = "Likely True / Well-Supported"
    elif score >= 65:
        verdict = "Plausible / Needs Verification"
    elif score >= 40:
        verdict = "Questionable / High Uncertainty"
    else:
        verdict = "Likely False / Misleading"

    return jsonify({
        "truth_score": score,
        "verdict": verdict,
        "explanation": "Demo score from TruCite backend (not for production use).",
        "input_preview": text[:120],
        "meta": {
            "length": length,
            "model": "unknown"
        }
    })


if __name__ == "__main__":
    # This is only used if you run app.py directly;
    # on Render, gunicorn will import app:app
    app.run(host="0.0.0.0", port=10000)
