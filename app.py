from flask import Flask, request, jsonify
from flask_cors import CORS

app = Flask(__name__)

# Allow all origins (fine for MVP; we can tighten later)
CORS(app, resources={r"/*": {"origins": "*"}})


@app.route("/health", methods=["GET"])
def health():
    """Simple health check."""
    return jsonify({"status": "ok"}), 200


@app.route("/truth-score", methods=["POST"])
def truth_score():
    """
    Demo truth scoring endpoint for TruCite.

    Expects JSON:
    {
      "text": "...",
      "model": "gpt-4.1"  (optional)
    }
    """
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    model = (data.get("model") or "unknown").strip()

    # Basic guardrails
    if not text:
        return jsonify(
            {
                "error": "Missing 'text' field in request body.",
                "truth_score": None,
                "verdict": "Invalid input",
            }
        ), 400

    # --------- DEMO HEURISTIC LOGIC (MVP ONLY) ---------
    # This is NOT real fact-checking yet — just a placeholder
    # so we have a stable API + UI. We’ll replace this
    # with the real TruCite engine later.

    base_score = 80  # start optimistic

    lowered = text.lower()

    # obvious red-flag phrases → big penalty
    red_flags = [
        "earth is flat",
        "world is flat",
        "moon is made of cheese",
        "vaccines cause autism",
        "the sky is green on a clear day",
    ]
    if any(flag in lowered for flag in red_flags):
        base_score -= 40

    # if user provides sources/links, give a small boost
    has_source_link = "http://" in lowered or "https://" in lowered
    if has_source_link:
        base_score += 5

    # clamp between 0–100
    truth_score = max(0, min(100, base_score))

    # verdict buckets
    if truth_score >= 85:
        verdict = "Likely True / Well Supported"
    elif truth_score >= 60:
        verdict = "Plausible / Needs Verification"
    elif truth_score >= 40:
        verdict = "Questionable / High Uncertainty"
    else:
        verdict = "Likely False or Misleading"

    explanation = (
        "Demo score from TruCite backend (not for production use). "
        "This endpoint is wired only to showcase the verification UI, "
        "API contract, and model-agnostic scoring pipeline."
    )

    response = {
        "truth_score": truth_score,
        "verdict": verdict,
        "explanation": explanation,
        "input_preview": text[:200],
        "meta": {
            "model": model,
            "length": len(text),
            "has_sources": has_source_link,
        },
    }

    return jsonify(response), 200


# Local dev only; Render will run via gunicorn app:app
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
