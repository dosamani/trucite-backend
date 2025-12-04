from flask import Flask, request, jsonify

app = Flask(__name__)


# ---- CORS: allow calls from your Neocities demo ----
@app.after_request
def add_cors_headers(response):
    # Lock to your Neocities origin
    response.headers["Access-Control-Allow-Origin"] = "https://trucite-sandbox.neocities.org"
    response.headers["Vary"] = "Origin"

    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return response


@app.route("/")
def health():
    return "TruCite backend OK", 200


@app.route("/truth-score", methods=["GET", "POST", "OPTIONS"])
def truth_score():
    # Handle CORS preflight
    if request.method == "OPTIONS":
        return ("", 204)

    # --- Accept both GET and POST ---

    if request.method == "GET":
        # ?text=... from query string
        text = (request.args.get("text") or "").strip()
    else:  # POST with JSON { "text": "..." }
        data = request.get_json(silent=True) or {}
        text = (data.get("text") or "").strip()

    length = len(text)

    # Very simple toy scoring logic for demo
    base = 50
    score = base
    if length > 0:
        score += min(length // 20, 30)
    score = max(0, min(score, 100))

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
    # Render ignores this line and uses gunicorn, but it's fine for local runs
    app.run(host="0.0.0.0", port=10000)
