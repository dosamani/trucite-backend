from flask import Flask, request, jsonify, make_response
from flask_cors import CORS
import os

app = Flask(__name__)

CORS(
    app,
    resources={r"/*": {"origins": "*"}},
    methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"]
)

@app.route("/", methods=["GET"])
def health():
    return "TruCite backend OK", 200

@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    if request.method == "OPTIONS":
        return make_response("", 204)

    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    return jsonify({
        "mode": "demo",
        "score": 82,
        "verdict": "Likely reliable"
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
