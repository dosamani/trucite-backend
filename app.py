from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/")
def health():
    return "TruCite backend OK", 200

@app.route("/api/score", methods=["POST"])
def score():
    data = request.get_json(force=True)
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "score": 0,
            "verdict": "No input provided",
            "details": "Empty input"
        }), 400

    # DEMO logic (replace later)
    score = 82
    verdict = "Likely reliable (demo)"
    explanation = (
        "This is a placeholder TruCite response. "
        "Structural integrity appears intact, uncertainty language is present, "
        "and no obvious hallucination patterns were detected."
    )

    return jsonify({
        "score": score,
        "verdict": verdict,
        "details": explanation
    })
