# app.py
from flask import Flask, request, jsonify
from flask_cors import CORS

# NEW: reference grounding hook
# Make sure you create reference_engine.py in the same folder.
from reference_engine import find_references

app = Flask(__name__)
CORS(app)


def parse_claims(text: str):
    """
    MVP claim parser:
    - Splits text into simple claim candidates
    - Keeps it intentionally basic for v1
    """
    if not text:
        return []

    # Split on new lines and periods as a crude first pass
    raw_parts = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        raw_parts.extend([p.strip() for p in line.split(".") if p.strip()])

    # Limit to avoid abuse in MVP
    return raw_parts[:10]


def heuristic_truth_score(text: str):
    """
    MVP heuristic score (placeholder):
    - Returns a mid-range score with small adjustments
    - This is NOT a real truth engine yet
    """
    if not text or len(text.strip()) < 5:
        return 20

    t = text.strip().lower()

    # Small penalties for obvious nonsense keywords (demo only)
    nonsense_markers = ["made up of candy", "made up of fudge", "completely made up of"]
    penalty = 0
    for m in nonsense_markers:
        if m in t:
            penalty += 10

    # Small boost for numbers (often correlated with factual claims—but not always)
    has_number = any(ch.isdigit() for ch in t)
    boost = 5 if has_number else 0

    base = 55
    score = base + boost - penalty

    # Clamp 0–100
    score = max(0, min(100, score))
    return score


def verdict_from_score(score: int):
    """
    Simple mapping for MVP UI.
    Keep your existing strings stable for now.
    """
    if score >= 85:
        return "Likely True / Low Uncertainty"
    if score >= 70:
        return "Probably True / Moderate Uncertainty"
    if score >= 50:
        return "Questionable / High Uncertainty"
    return "Likely False / High Uncertainty"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/truth-score", methods=["POST"])
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    # 1) Parse claims
    claims = parse_claims(text)

    # 2) Reference grounding (MVP deterministic hook)
    references = find_references(claims)

    # 3) Score (still heuristic baseline for v1)
    score = int(heuristic_truth_score(text))
    verdict = verdict_from_score(score)

    response = {
        "mode": "mvp_v1",
        "truth_score": score,
        "verdict": verdict,
        "explanation": "MVP score based on heuristic baseline; reference grounding is in early deterministic mode.",
        "references": references
    }

    return jsonify(response), 200


if __name__ == "__main__":
    # Local run (Render uses gunicorn, so this is for local dev)
    app.run(host="0.0.0.0", port=5000, debug=False)
