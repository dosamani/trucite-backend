import os
from flask import Flask, request, jsonify

app = Flask(__name__)

# ---------- CORS (DIRECT FIX) ----------
# Neocities is a different origin, so browser sends a preflight OPTIONS for JSON POST.
# We must respond to OPTIONS with the right headers, and also include headers on POST responses.

ALLOWED_ORIGINS = {
    "https://trucite-sandbox.neocities.org",
    "https://trucite.ai",          # keep if you later point domain here
    "https://www.trucite.ai",
}

def cors_headers(origin):
    # If origin is missing (curl/server-to-server), allow none or "*".
    # For browser, echo back allowed origin.
    if origin in ALLOWED_ORIGINS:
        return {
            "Access-Control-Allow-Origin": origin,
            "Vary": "Origin",
            "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
            "Access-Control-Allow-Headers": "Content-Type, Authorization",
            "Access-Control-Max-Age": "86400",
        }
    # fallback: allow Neocities-style testing if origin is unknown
    # (You can tighten later)
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS, GET",
        "Access-Control-Allow-Headers": "Content-Type, Authorization",
        "Access-Control-Max-Age": "86400",
    }

@app.after_request
def add_cors(response):
    origin = request.headers.get("Origin", "")
    headers = cors_headers(origin)
    for k, v in headers.items():
        response.headers[k] = v
    return response


# ---------- Health ----------
@app.get("/")
def root():
    return jsonify({"status": "TruCite backend ok"}), 200

@app.get("/health")
def health():
    return jsonify({"ok": True}), 200


# ---------- API: score ----------
@app.route("/api/score", methods=["POST", "OPTIONS"])
def api_score():
    # Handle preflight
    if request.method == "OPTIONS":
        # empty 204 is fine; after_request will add CORS headers
        return ("", 204)

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "error": "Missing 'text' in JSON body",
            "expected": {"text": "AI-generated output to verify"}
        }), 400

    # --- MVP scoring placeholder (replace later) ---
    # Keep deterministic + simple so demo doesn't look broken.
    # You can swap this with your real scoring engine later.
    length = len(text)
    score = 50
    if length > 400:
        score = 68
    if any(k in text.lower() for k in ["study", "trial", "meta-analysis", "randomized", "doi", "pmid"]):
        score = min(92, score + 18)
    if any(k in text.lower() for k in ["definitely", "guaranteed", "always", "never"]):
        score = max(35, score - 15)

    verdict = verdict_from_score(score)

    response = {
        "truth_score": score,
        "verdict": verdict,
        "explanation": "MVP placeholder scoring. Backend is live and CORS-enabled. Replace logic with your scoring engine next.",
        "signals": {
            "text_length": length,
            "contains_citation_terms": any(k in text.lower() for k in ["doi", "pmid", "arxiv", "journal", "study", "trial"]),
            "contains_overconfidence_terms": any(k in text.lower() for k in ["definitely", "guaranteed", "always", "never"]),
        },
        "references": [],
        "mode": "render-direct"
    }
    return jsonify(response), 200


def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"


if __name__ == "__main__":
    # For local testing only
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
