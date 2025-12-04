from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# Allow calls from your Neocities sandbox
CORS(app, resources={r"/*": {"origins": "https://trucite-sandbox.neocities.org"}})

@app.route("/")
def health():
    return "TruCite backend OK", 200


@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    # Handle CORS preflight
    if request.method == "OPTIONS":
        return "", 204

    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    # Very simple demo scoring logic
    length = len(text)
    base = 50
    score = max(0, min(100, base + min(length // 20, 30)))

    verdict = "Questionable / High Uncertainty"
    if score >= 85:
        verdict = "Likely True / Well-Supported"
    elif score >= 65:
        verdict = "Plausible / Needs Verification"
    elif score < 40:
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
    app.run(host="0.0.0.0", port=10000)
