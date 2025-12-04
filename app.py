from flask import Flask, request, jsonify

app = Flask(__name__)


# ----- CORS: allow browser calls from Neocities -----
@app.after_request
def add_cors_headers(response):
    # For demo, allow all origins
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    return response


@app.route("/")
def health():
    return "TruCite backend OK", 200


@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    # Handle preflight OPTIONS request
    if request.method == "OPTIONS":
        # CORS headers already added by after_request
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    length = len(text)

    # --- very simple demo scoring logic ---
    base = 50
    # add up to +30 for longer text
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
            "meta": {
                "length": length,
                "model": "unknown",
            },
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
