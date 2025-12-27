from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from datetime import datetime
import hashlib
import uuid

from claim_parser import extract_claims
from reference_engine import analyze_claims

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# ---------- Health ----------
@app.route("/health")
def health():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": datetime.utcnow().isoformat() + "Z"
    })

# ---------- Landing Page ----------
@app.route("/")
def index():
    return send_from_directory("static", "index.html")

# ---------- Core Verification ----------
@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json()
    text = data.get("text", "")

    event_id = f"evt_{uuid.uuid4().hex[:12]}"
    claims = extract_claims(text)
    analysis = analyze_claims(claims)

    payload = {
        "event_id": event_id,
        "input": text,
        "claims": claims,
        "explanation": analysis["explanation"],
        "final_score": analysis["final_score"],
        "verdict": analysis["verdict"],
        "confidence_curve": analysis.get("confidence_curve", [])
    }

    payload_str = str(payload)
    event_hash = hashlib.sha256(payload_str.encode()).hexdigest()
    payload["event_hash"] = event_hash

    return jsonify(payload)

# ---------- Run ----------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
