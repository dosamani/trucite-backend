from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# ✅ Allow browser calls from Neocities (and any future frontend) to this API
# This also handles preflight OPTIONS automatically.
CORS(
    app,
    resources={r"/api/*": {"origins": "*"}},
    supports_credentials=False,
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

@app.get("/")
def home():
    return "TruCite backend is running. Use POST /api/score", 200

@app.route("/api/score", methods=["POST", "OPTIONS"])
def score():
    # Preflight (browser sends OPTIONS first)
    if request.method == "OPTIONS":
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = str(data.get("text", "")).strip()

    if not text:
        return jsonify({"mode": "error", "score": 0, "verdict": "No input provided"}), 400

    # --- DEMO scoring logic (replace later) ---
    # Simple heuristic so your demo is not always 82:
    # Longer + more specific-looking text -> higher score, very short -> lower
    length = len(text)
    if length < 20:
        score = 25
    elif length < 80:
        score = 55
    elif length < 200:
        score = 72
    else:
        score = 84

    verdict = (
        "Likely True / Well-Supported" if score >= 85 else
        "Plausible / Needs Verification" if score >= 65 else
        "Questionable / High Uncertainty" if score >= 40 else
        "Likely False / Misleading"
    )

    return jsonify({
        "mode": "demo",
        "score": score,
        "verdict": verdict
    }), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
