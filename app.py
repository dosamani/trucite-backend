import os
import uuid
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder="static", static_url_path="/static")


@app.route("/", methods=["GET"])
def home():
    # Always serve the landing page from /static/index.html
    return send_from_directory("static", "index.html")


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"})


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # MVP placeholder response
    event_id = str(uuid.uuid4())
    score = 54
    verdict = "Questionable / High Uncertainty"

    return jsonify({
        "event_id": event_id,
        "score": score,
        "verdict": verdict,
        "audit_fingerprint": {
            "timestamp_utc": datetime.now(timezone.utc).isoformat()
        },
        "claims": [{"text": text}]
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "10000")))
