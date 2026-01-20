from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import hashlib
import datetime
import re
import uuid

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app)

# In-memory drift store (MVP)
prior_events = {}

# ---------- ROUTES ----------

@app.route("/")
def serve_index():
    return send_from_directory("static", "index.html")


@app.route("/health")
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "No text provided"}), 400

    # --- Fingerprint ---
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    event_id = sha[:12]
    timestamp = datetime.datetime.utcnow().isoformat() + "Z"

    # --- Basic claim split (MVP) ---
    claims = split_claims(text)

    scored_claims = []
    overall_score = 100

    for c in claims:
        signals = extract_signals(c)
        risk_tags = []

        # Risk rules
        if signals["has_numerics"] and not signals["has_url"]:
            risk_tags.append("numeric_claim")

        if signals["has_citation_like"] and not signals["has_url"]:
            risk_tags.append("citation_unverified")

        # Score logic
        score = 100
        if signals["has_numerics"]:
            score -= 20
        if signals["has_citation_like"] and not signals["has_url"]:
            score -= 20
        if signals["hedge_count"] == 0:
            score -= 5

        score = max(20, min(100, score))

        verdict = classify_verdict(score)

        scored_claims.append({
            "text": c,
            "score": score,
            "verdict": verdict,
            "risk_tags": risk_tags,
            "signals": signals
        })

        overall_score = min(overall_score, score)

    # --- Drift (MVP in-memory) ---
    drift = {
        "has_prior": text in prior_events,
        "drift_flag": False,
        "score_delta": None,
        "verdict_changed": False,
        "prior_timestamp_utc": None,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time.",
        "claim_count_delta": None
    }

    if text in prior_events:
        prior = prior_events[text]
        drift["prior_timestamp_utc"] = prior["timestamp"]
        drift["score_delta"] = overall_score - prior["score"]
        drift["verdict_changed"] = classify_verdict(overall_score) != prior["verdict"]
        drift["claim_count_delta"] = len(claims) - prior["claim_count"]

        if abs(drift["score_delta"]) > 15:
            drift["drift_flag"] = True

    # Save this run
    prior_events[text] = {
        "timestamp": timestamp,
        "score": overall_score,
        "verdict": classify_verdict(overall_score),
        "claim_count": len(claims)
    }

    response = {
        "event_id": event_id,
        "input": {
            "length_chars": len(text),
            "num_claims": len(claims)
        },
        "score": overall_score,
        "verdict": classify_verdict(overall_score),
        "audit_fingerprint": {
            "sha256": sha,
            "timestamp_utc": timestamp
        },
        "claims": scored_claims,
        "drift": drift,
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, "
            "numeric/stat patterns, citation signals, absolute language, and uncertainty cues. "
            "Enterprise mode adds evidence-backed checks, source validation, and persistent drift analytics."
        )
    }

    return jsonify(response), 200


# ---------- HELPERS ----------

def split_claims(text: str):
    parts = re.split(r"[.;]\s*", text)
    return [p.strip() for p in parts if p.strip()]


def extract_signals(text: str):
    has_numerics = bool(re.search(r"\d", text))
    has_percent = "%" in text
    has_url = bool(re.search(r"https?://", text))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", text))
    has_doi = bool(re.search(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", text))
    hedge_count = len(re.findall(r"\b(may|might|could|possibly|likely|probably)\b", text, re.I))
    has_citation_like = bool(re.search(r"\(\d{4}\)|PMID|DOI", text))

    return {
        "has_numerics": has_numerics,
        "numeric_count": len(re.findall(r"\d+", text)),
        "has_percent": has_percent,
        "has_url": has_url,
        "has_year": has_year,
        "has_doi": has_doi,
        "hedge_count": hedge_count,
        "has_citation_like": has_citation_like
    }


def classify_verdict(score):
    if score >= 80:
        return "Likely reliable"
    elif score >= 60:
        return "Unclear / needs verification"
    else:
        return "High risk / do not rely"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
