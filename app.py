import os
import time
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

try:
    from claim_parser import extract_claims
except Exception:
    extract_claims = None

try:
    from reference_engine import score_claim_text
except Exception:
    score_claim_text = None


app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)


@app.get("/")
def landing():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)


@app.get("/health")
def health():
    return jsonify({"status": "ok", "service": "trucite-backend", "ts": int(time.time())})


@app.route("/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    evidence = (payload.get("evidence") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' in request body"}), 400

    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    ts = datetime.now(timezone.utc).isoformat()

    claims = []
    if extract_claims:
        try:
            extracted = extract_claims(text)
            if isinstance(extracted, list):
                for c in extracted:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c["text"])})
                    elif isinstance(c, str):
                        claims.append({"text": c})
            elif isinstance(extracted, str):
                claims = [{"text": extracted}]
        except Exception:
            claims = [{"text": text}]
    else:
        claims = [{"text": text}]

    if score_claim_text:
        try:
            score, verdict, explanation = score_claim_text(text)
        except Exception:
            score, verdict, explanation = heuristic_score(text, evidence)
    else:
        score, verdict, explanation = heuristic_score(text, evidence)

    if score >= 75:
        action = "ALLOW"
        reason = "High confidence per current MVP scoring."
    elif score >= 55:
        action = "REVIEW"
        reason = "Medium confidence. Human verification recommended."
    else:
        action = "BLOCK"
        reason = "Low confidence. Do not use without verification."

    resp = {
        "verdict": verdict,
        "score": int(score),
        "decision": {"action": action, "reason": reason},
        "event_id": event_id,
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": ts
        },
        "claims": claims,
        "explanation": explanation
    }

    return jsonify(resp), 200


def heuristic_score(text: str, evidence: str = ""):
    t = (text or "").lower()
    ev = (evidence or "").strip()

    risky = ["always", "never", "guaranteed", "cure", "100%", "proof", "definitely"]
    hedges = ["may", "might", "could", "likely", "possibly", "suggests", "uncertain"]

    score = 55

    if any(w in t for w in risky):
        score -= 15

    if any(w in t for w in hedges):
        score += 10

    if len(text) > 800:
        score -= 10

    has_digit = any(ch.isdigit() for ch in text)
    if has_digit and not ev:
        score -= 18
    if has_digit and ev:
        score += 8

    if len(text) < 140 and not has_digit:
        if " is " in t or " are " in t:
            score += 25

    score = max(0, min(100, score))

    if score >= 75:
        verdict = "Likely true / consistent"
    elif score >= 55:
        verdict = "Unclear / needs verification"
    else:
        verdict = "High risk of error / hallucination"

    explanation = (
        "MVP heuristic score. This demo evaluates linguistic certainty and uncertainty cues, "
        "basic risk signals, and applies conservative handling for numeric or liability claims "
        "unless evidence is provided. Replace with evidence-backed verification in production."
    )

    return score, verdict, explanation


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")))
