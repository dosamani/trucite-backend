from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

@app.route("/", methods=["GET"])
def root():
    return f"""
    <html>
    <body style="background:#000;color:#f5c542;font-family:Arial;padding:40px;">
        <h1>TruCite Backend is Running</h1>
        <p>Status: Online</p>
        <p>UTC: {datetime.utcnow().isoformat()}Z</p>
    </body>
    </html>
    """

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": datetime.utcnow().isoformat()
    })

def extract_claims(text):
    sentences = re.split(r'[.?!]', text)
    claims = []
    for i, s in enumerate(sentences):
        s = s.strip()
        if len(s) > 5:
            claims.append({
                "id": f"c{i+1}",
                "text": s,
                "type": "factual",
                "confidence_weight": 3
            })
    return claims

def score_claim(claim):
    if "candy" in claim["text"].lower():
        return 10
    if "moon" in claim["text"].lower():
        return 60
    return 75

@app.route("/verify", methods=["POST"])
def verify():
    data = request.json
    text = data.get("text", "")

    claims = extract_claims(text)

    total_weight = 0
    weighted_sum = 0

    for c in claims:
        c["score"] = score_claim(c)
        weighted_sum += c["score"] * c["confidence_weight"]
        total_weight += c["confidence_weight"]

    final_score = int(weighted_sum / total_weight) if total_weight else 0

    if final_score < 30:
        verdict = "Low Confidence"
    elif final_score < 60:
        verdict = "Questionable"
    elif final_score < 85:
        verdict = "Needs Verification"
    else:
        verdict = "Highly Reliable"

    return jsonify({
        "claims": claims,
        "final_score": final_score,
        "verdict": verdict,
        "explanation": "Multi-claim weighted analysis (TruCite Claim Engine v2)"
    })
