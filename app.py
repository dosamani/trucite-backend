
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

@app.route("/")
def home():
    return send_from_directory("static", "index.html")

@app.route("/api/score", methods=["POST"])
def score():
    data = request.get_json()
    text = data.get("text", "")

    score = 82 if len(text) > 20 else 35
    verdict = "Likely reliable" if score >= 70 else "Questionable"

    return jsonify({
        "score": score,
        "verdict": verdict
    })

if __name__ == "__main__":
    app.run()
