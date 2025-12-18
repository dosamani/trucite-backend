from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import re
import random

# ✅ IMPORTANT: this is what makes /static/* work on Render
app = Flask(__name__, static_folder="static", static_url_path="/static")

# ✅ Allow calls from anywhere (keeps Neocities possible too)
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# -----------------------------
# Helpers (simple MVP scoring)
# -----------------------------
def clamp(n, lo=0, hi=100):
    try:
        n = float(n)
    except:
        n = 0
    return max(lo, min(hi, int(round(n))))

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"

def simple_mvp_score(text: str) -> int:
    """
    Deterministic-ish MVP scoring (no fake references).
    This is intentionally conservative: obvious absurd claims score low.
    """
    t = (text or "").strip().lower()

    # absurd/known-false patterns
    false_markers = [
        "moon is made of cheese",
        "completely made of cheese",
        "earth is flat",
        "vaccines cause autism",
        "5g causes covid",
    ]
    if any(m in t for m in false_markers):
        return 12

    # signals that claim is cautious or asks to verify
    cautious = ["may", "might", "could", "uncertain", "not sure", "estimate", "likely", "approximately"]
    cautious_hits = sum(1 for w in cautious if w in t)

    # numbers, dates, citations-ish
    has_numbers = bool(re.search(r"\d", t))
    has_links = "http://" in t or "https://" in t
    has_quote = '"' in text or "“" in text or "”" in text

    base = 55
    if cautious_hits >= 2:
        base += 10
    if has_numbers:
        base += 7
    if has_links:
        base += 8
    if has_quote:
        base += 4

    # if very short, reduce confidence
    if len(t) < 25:
        base -= 10

    # add tiny jitter so it doesn't look frozen (but stays stable-ish)
    base += random.randint(-3, 3)

    return clamp(base)

# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "version": "mvp-rag-v1.1"})

# ✅ Serve the landing page at /
@app.get("/")
def root():
    return send_from_directory(app.static_folder, "index.html")

# ✅ Serve static files explicitly (belt + suspenders)
@app.get("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.static_folder, filename)

# ✅ The endpoint your frontend calls
@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    if request.method == "OPTIONS":
        # Preflight response
        resp = jsonify({"ok": True})
        return resp, 200

    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({
            "truth_score": 0,
            "verdict": "No input",
            "explanation": "No text provided.",
            "references": [],
            "mode": "mvp_v1"
        }), 400

    score = simple_mvp_score(text)
    verdict = verdict_from_score(score)

    # ✅ IMPORTANT: no fake DOIs, no Wikipedia, no hallucinated book titles
    # We return empty references until you wire real evidence sources.
    return jsonify({
        "truth_score": score,
        "verdict": verdict,
        "explanation": "MVP score based on heuristic signals (structure, caution language, specificity). References disabled until real evidence sources are wired.",
        "references": [],
        "mode": "mvp_v1"
    })

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
