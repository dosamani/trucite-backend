from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS
import hashlib
import datetime
import re
import traceback

app = Flask(__name__, static_folder="static", static_url_path="/static")
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

prior_events = {}

# -----------------------
# ROUTES
# -----------------------

@app.route("/", methods=["GET"])
def serve_index():
    # Your index.html is inside /static
    return send_from_directory("static", "index.html")

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200

@app.route("/routes", methods=["GET"])
def routes():
    # Lets us confirm which routes are actually deployed
    out = []
    for rule in app.url_map.iter_rules():
        out.append({
            "rule": str(rule),
            "methods": sorted([m for m in rule.methods if m not in ("HEAD", "OPTIONS")])
        })
    return jsonify({"routes": sorted(out, key=lambda x: x["rule"])}), 200

# IMPORTANT: Support BOTH /verify and /api/verify to avoid 404s
@app.route("/verify", methods=["POST", "OPTIONS"])
@app.route("/api/verify", methods=["POST", "OPTIONS"])
def verify():
    if request.method == "OPTIONS":
        return _cors_preflight_ok()

    try:
        data = request.get_json(silent=True) or {}

        text = (
            (data.get("text") or "")
            or (data.get("claim") or "")
            or (data.get("input") or "")
            or (data.get("content") or "")
        ).strip()

        if not text:
            return jsonify({
                "error": "No text provided",
                "expected_keys": ["text", "claim", "input", "content"]
            }), 400

        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        event_id = sha[:12]
        timestamp_utc = datetime.datetime.utcnow().isoformat() + "Z"

        claims = split_claims(text)

        scored_claims = []
        overall_score = 100

        for c in claims:
            signals = extract_signals(c)
            risk_tags = []

            if signals["has_numerics"]:
                risk_tags.append("numeric_claim")

            if signals["has_citation_like"] and not (signals["has_url"] or signals["has_doi"]):
                risk_tags.append("citation_unverified")

            score = 100

            if signals["has_numerics"]:
                score -= 18

            if "citation_unverified" in risk_tags:
                score -= 25

            if signals["absolute_count"] > 0:
                score -= min(12, 3 * signals["absolute_count"])

            if signals["hedge_count"] == 0:
                score -= 5

            score = max(20, min(100, score))
            verdict = classify_verdict(score)

            claim_type = "general_claim"
            evidence_needed = {"required": False}

            if signals["has_numerics"] or "citation_unverified" in risk_tags:
                claim_type = "numeric_or_stat_claim"
                evidence_needed = {
                    "required": True,
                    "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI) or provided evidence.",
                    "acceptable_evidence_examples": [
                        "Peer-reviewed paper link (DOI/PMID/URL)",
                        "Clinical guideline link (e.g., society guideline URL)",
                        "Regulatory label / official statement URL",
                        "Internal policy document reference (enterprise mode)"
                    ],
                    "suggested_query": f"{c} clinical trial meta-analysis PMID"
                }

            scored_claims.append({
                "text": c,
                "score": score,
                "verdict": verdict,
                "risk_tags": risk_tags,
                "signals": signals,
                "claim_type": claim_type,
                "evidence_needed": evidence_needed
            })

            overall_score = min(overall_score, score)

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
            drift["prior_timestamp_utc"] = prior["timestamp_utc"]
            drift["score_delta"] = overall_score - prior["score"]
            drift["verdict_changed"] = classify_verdict(overall_score) != prior["verdict"]
            drift["claim_count_delta"] = len(claims) - prior["claim_count"]
            if drift["score_delta"] is not None and abs(drift["score_delta"]) >= 15:
                drift["drift_flag"] = True

        prior_events[text] = {
            "timestamp_utc": timestamp_utc,
            "score": overall_score,
            "verdict": classify_verdict(overall_score),
            "claim_count": len(claims)
        }

        response = {
            "event_id": event_id,
            "input": {"length_chars": len(text), "num_claims": len(claims)},
            "score": overall_score,
            "verdict": classify_verdict(overall_score),
            "audit_fingerprint": {"sha256": sha, "timestamp_utc": timestamp_utc},
            "claims": scored_claims,
            "drift": drift,
            "uncertainty_map": {
                "risk_tags": list({tag for cl in scored_claims for tag in cl.get("risk_tags", [])})
            },
            "explanation": (
                "MVP heuristic verification. This demo flags risk via claim segmentation, "
                "numeric/stat patterns, citation signals, absolute language, and uncertainty cues. "
                "Enterprise mode adds evidence-backed checks, source validation, and persistent drift analytics."
            )
        }

        return jsonify(response), 200

    except Exception as e:
        err = {
            "error": "Backend exception in /verify",
            "message": str(e),
            "trace": traceback.format_exc().splitlines()[-12:]
        }
        return jsonify(err), 500


# -----------------------
# HELPERS
# -----------------------

def _cors_preflight_ok():
    resp = make_response("", 200)
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    return resp

def split_claims(text: str):
    parts = re.split(r"[.;]\s*|\n+", text)
    return [p.strip() for p in parts if p.strip()]

def extract_signals(text: str):
    has_numerics = bool(re.search(r"\d", text))
    numeric_count = len(re.findall(r"\d+(?:\.\d+)?", text))
    has_percent = "%" in text

    has_url = bool(re.search(r"https?://", text, re.I))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", text))
    has_doi = bool(re.search(r"\b10\.\d{4,9}/[-._;()/:A-Za-z0-9]+\b", text))

    hedge_count = len(re.findall(r"\b(may|might|could|possibly|likely|probably|suggests|appears)\b", text, re.I))
    absolute_count = len(re.findall(r"\b(always|never|guaranteed|proves|definitely|certainly|must)\b", text, re.I))

    has_citation_like = bool(re.search(r"\(\s*\d{4}\s*\)|\bPMID\b|\bDOI\b|\bPubMed\b", text, re.I))

    return {
        "has_numerics": has_numerics,
        "numeric_count": numeric_count,
        "has_percent": has_percent,
        "has_url": has_url,
        "has_year": has_year,
        "has_doi": has_doi,
        "hedge_count": hedge_count,
        "absolute_count": absolute_count,
        "has_citation_like": has_citation_like,
        "has_sources_provided": has_url or has_doi
    }

def classify_verdict(score: int):
    if score >= 80:
        return "Likely reliable"
    if score >= 60:
        return "Unclear / needs verification"
    return "High risk / do not rely"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
