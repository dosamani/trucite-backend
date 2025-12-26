
from flask import Flask, request, jsonify
from datetime import datetime
import re

app = Flask(__name__)

# --- Minimal "seed reference corpus" (MVP grounding) ---
# Later we replace/augment this with real retrieval + citations.
SEED_REFERENCES = [
    {
        "keywords": ["apollo", "1969", "moon", "landed", "armstrong", "aldrin"],
        "title": "NASA Apollo 11 Mission Overview",
        "url": "https://www.nasa.gov/mission/apollo-11/",
        "snippet": "Apollo 11 landed on the Moon in July 1969. Neil Armstrong and Buzz Aldrin walked on the lunar surface."
    },
    {
        "keywords": ["moon", "composition", "rock", "regolith", "geology"],
        "title": "NASA - Moon Facts / Overview",
        "url": "https://science.nasa.gov/moon/",
        "snippet": "The Moon is a rocky body with a surface covered by regolith and impact craters; it is not composed of candy."
    },
    {
        "keywords": ["candy", "made of candy", "moon is made of candy"],
        "title": "Scientific Consensus (General): The Moon is rock, not candy",
        "url": "https://science.nasa.gov/moon/",
        "snippet": "Widely established: the Moon is primarily silicate rock and metal; 'made of candy' is not supported."
    }
]

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
        "time_utc": datetime.utcnow().isoformat(),
        "routes": ["/", "/health", "/verify"]
    })

def extract_claims(text):
    # Split into sentence-like claims (MVP)
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

def score_claim(claim_text):
    t = claim_text.lower()

    # Deterministic heuristic scoring (still MVP)
    if "made of candy" in t or ("moon" in t and "candy" in t):
        return 10
    if "humans" in t and "moon" in t and "1969" in t:
        return 92
    if "moon" in t:
        return 60
    return 75

def ground_references(claim_text):
    """
    MVP grounding: keyword-match claim to seed references.
    Later versions:
      - vector retrieval
      - citation ranking
      - multi-source corroboration
      - integrity chain hashing
    """
    t = claim_text.lower()
    hits = []

    for ref in SEED_REFERENCES:
        matched = 0
        for kw in ref["keywords"]:
            if kw in t:
                matched += 1

        # simple threshold: at least 1 keyword match
        if matched >= 1:
            hits.append({
                "title": ref["title"],
                "url": ref["url"],
                "match": ref["snippet"]
            })

    # If no match, return an empty list (explicitly)
    return hits

@app.route("/verify", methods=["POST"])
def verify():
    data = request.json or {}
    text = data.get("text", "")

    claims = extract_claims(text)

    total_weight = 0
    weighted_sum = 0

    for c in claims:
        c_score = score_claim(c["text"])
        c["score"] = c_score
        c["references"] = ground_references(c["text"])

        weighted_sum += c_score * c["confidence_weight"]
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
        "explanation": "Claim Engine v2 + MVP Reference Grounding (seed corpus keyword match)."
    })
