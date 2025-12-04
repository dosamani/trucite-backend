from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# Let flask-cors handle all CORS + OPTIONS preflight
CORS(app, resources={r"/*": {"origins": "*"}})


@app.route("/")
def health():
    return "TruCite backend OK", 200


@app.route("/truth-score", methods=["POST"])
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    length = len(text)

    # --- simple demo scoring logic ---
    base = 50
    score = base + min(length // 20, 30)
    score = max(0, min(100, score))

    if score >= 85:
        verdict = "Likely True / Well-Supported"
    elif score >= 65:
        verdict = "Plausible / Needs Verification"
    elif score >= 40:
        verdict = "Questionable / High Uncertainty"
    else:
        verdict = "Likely False / Misleading"

    return jsonify(
        {
            "truth_score": score,
            "verdict": verdict,
            "explanation": "Demo score from TruCite backend (not for production use).",
            "input_preview": text[:120],
            "meta": {"length": length, "model": "unknown"},
        }
    )


if __name__ == "__main__":
    # This is fine for Render; they override the port
    import os

    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
