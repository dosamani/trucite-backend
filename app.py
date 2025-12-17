
from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# ✅ Allow CORS from anywhere for MVP demo
# (Later you can restrict to https://trucite-sandbox.neocities.org)
CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    supports_credentials=False,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"]
)

@app.get("/")
def health():
    return "TruCite backend ok", 200

@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    # ✅ Explicitly handle preflight
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()

    if not text:
        return jsonify({
            "truth_score": 0,
            "verdict": "No input",
            "explanation": "No text provided.",
            "references": []
        }), 200

    # --- MVP heuristic scoring (placeholder until real verification engine) ---
    score = 55
    lower = text.lower()

    # light heuristics
    if any(x in lower for x in ["according to", "study", "randomized", "meta-analysis", "systematic review"]):
        score += 10
    if any(x in lower for x in ["i think", "maybe", "might", "could be", "not sure"]):
        score -= 8
    if any(x in lower for x in ["definitely", "guaranteed", "always", "never"]):
        score -= 6
    if any(x in lower for x in ["http://", "https://", "doi:"]):
        score += 12
    if len(text) > 800:
        score += 5

    score = max(0, min(100, score))

    verdict = (
        "Likely True / Well-Supported" if score >= 85 else
        "Plausible / Needs Verification" if score >= 65 else
        "Questionable / High Uncertainty" if score >= 40 else
        "Likely False / Misleading"
    )

    return jsonify({
        "truth_score": score,
        "verdict": verdict,
        "explanation": "MVP heuristic score (placeholder). Next: evidence checks + provenance + drift tracking.",
        "references": []
    }), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
