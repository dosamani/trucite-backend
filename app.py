from flask import Flask, request, jsonify, Response
from datetime import datetime, timezone
import hashlib

app = Flask(__name__)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

# ---------- Core Pages ----------

@app.get("/")
def home():
    html = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <title>TruCite Backend</title>
</head>
<body style="font-family:Arial;background:#0b0b0b;color:#ffd54a;padding:24px;">
  <h2>TruCite Backend is Running</h2>
  <p>API Status: Online</p>
  <p>UTC: %s</p>
</body>
</html>""" % utc_now_iso()
    return Response(html, mimetype="text/html")

@app.get("/health")
def health():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": utc_now_iso(),
        "routes": ["/health", "/verify"]
    })

# ---------- Verification Engine (MVP) ----------

def extract_claims(text):
    return [{
        "id": "c1",
        "text": text,
        "type": "factual",
        "confidence_weight": 1
    }]

def compute_truth_score(text):
    # Simple deterministic MVP scoring
    h = int(hashlib.sha256(text.encode()).hexdigest(), 16)
    score = (h % 51) + 50   # 50–100 range
    return score

def verdict_from_score(score):
    if score >= 85:
        return "Likely True"
    if score >= 70:
        return "Plausible / Needs Verification"
    if score >= 50:
        return "Questionable / High Uncertainty"
    return "Likely False"

@app.post("/verify")
def verify():
    data = request.get_json(force=True)
    text = data.get("text", "").strip()

    if not text:
        return jsonify({"error": "Missing text"}), 400

    claims = extract_claims(text)
    score = compute_truth_score(text)
    verdict = verdict_from_score(score)

    return jsonify({
        "input": text,
        "claims": claims,
        "score": score,
        "verdict": verdict,
        "explanation": "MVP score based on deterministic heuristic. Reference grounding & drift tracking will be added next."
    })
