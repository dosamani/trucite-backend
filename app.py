
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from datetime import datetime, timezone
import re
import hashlib

app = Flask(__name__)
CORS(app)

# -----------------------------
# Version / Fingerprint
# -----------------------------
FINGERPRINT = "TRUCITE_BACKEND_HTML_FINGERPRINT_v20251226_STEP_4_2"
UTC_NOW = lambda: datetime.now(timezone.utc).isoformat()

# -----------------------------
# Seed corpus (MVP grounding)
# Replace / expand later with real sources.
# In Step 5.x we’ll load this from a DB or curated dataset.
# -----------------------------
SEED_CORPUS = [
    {
        "id": "ref_nasa_apollo",
        "title": "NASA - Apollo 11 Mission Overview",
        "url": "https://www.nasa.gov/",
        "snippet": "Apollo 11 was the first crewed mission to land on the Moon in July 1969.",
        "keywords": ["apollo", "apollo 11", "moon", "1969", "landed", "nasa"]
    },
    {
        "id": "ref_britannica_moon",
        "title": "Encyclopedia - Moon Overview",
        "url": "https://www.britannica.com/",
        "snippet": "The Moon is Earth’s natural satellite composed of rock; it is not made of candy.",
        "keywords": ["moon", "rock", "satellite", "composition", "not", "candy"]
    },
    {
        "id": "ref_wikipedia_apollo",
        "title": "Apollo 11 (overview)",
        "url": "https://en.wikipedia.org/wiki/Apollo_11",
        "snippet": "Apollo 11 landed on the Moon on July 20, 1969. Neil Armstrong and Buzz Aldrin walked on the lunar surface.",
        "keywords": ["apollo", "apollo 11", "moon", "1969", "armstrong", "aldrin", "landed"]
    }
]

# Simple “contradiction” cues for MVP conflict scoring
NEGATION_CUES = {"not", "no", "never", "false", "incorrect", "myth", "hoax", "debunk"}
AFFIRM_CUES = {"is", "are", "was", "were", "landed", "first", "confirmed", "evidence", "verified"}

# -----------------------------
# Helpers
# -----------------------------
def normalize_text(t: str) -> str:
    t = t or ""
    t = t.strip()
    t = re.sub(r"\s+", " ", t)
    return t

def split_into_sentences(text: str):
    # Lightweight sentence splitter
    text = normalize_text(text)
    if not text:
        return []
    parts = re.split(r"(?<=[\.\?\!])\s+", text)
    parts = [p.strip() for p in parts if p.strip()]
    return parts

def extract_claims(text: str):
    """
    Claim Engine v2 (MVP):
    - Split into sentences
    - Classify as 'factual' if it contains a verb-like cue
    - Assign a confidence_weight based on length and cue density (simple heuristic)
    """
    sentences = split_into_sentences(text)
    claims = []
    for idx, s in enumerate(sentences, start=1):
        s_l = s.lower()
        # MVP factual heuristic: contains "is/are/was/were/has/have/landed"
        factual = any(cue in s_l for cue in [" is ", " are ", " was ", " were ", " has ", " have ", " landed "])
        ctype = "factual" if factual else "other"

        # Weight: baseline 1, boost if numbers/dates, penalize ultra-short
        weight = 1.0
        if re.search(r"\b(19|20)\d{2}\b", s):  # year-like
            weight += 0.5
        if len(s) < 25:
            weight -= 0.2
        if "moon" in s_l:
            weight += 0.2
        weight = max(0.3, min(2.0, weight))

        claims.append({
            "id": f"c{idx}",
            "text": s,
            "type": ctype,
            "confidence_weight": round(weight, 2)
        })
    return claims

def keyword_set(s: str):
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    toks = [t for t in s.split() if t and len(t) > 2]
    return set(toks)

def match_seed_references(claim_text: str, top_k: int = 3):
    """
    MVP grounding: keyword overlap with SEED_CORPUS.
    Returns ranked list of refs with overlap metrics.
    """
    c_kw = keyword_set(claim_text)
    scored = []
    for ref in SEED_CORPUS:
        ref_kw = set(ref.get("keywords", [])) | keyword_set(ref.get("snippet", "")) | keyword_set(ref.get("title", ""))
        overlap = len(c_kw & ref_kw)
        union = max(1, len(c_kw | ref_kw))
        jaccard = overlap / union
        scored.append((overlap, jaccard, ref))

    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    results = []
    for overlap, jaccard, ref in scored[:top_k]:
        if overlap <= 0:
            continue
        results.append({
            "ref_id": ref["id"],
            "title": ref["title"],
            "url": ref["url"],
            "snippet": ref["snippet"],
            "overlap_terms": overlap,
            "match_strength": round(jaccard, 3)
        })
    return results

def detect_support_or_conflict(claim_text: str, ref_snippet: str):
    """
    MVP support/conflict:
    - If ref snippet includes negation cues and claim includes positive cue -> conflict-ish
    - If ref snippet shares key terms and aligns on cues -> support-ish
    Returns: "support" | "conflict" | "neutral"
    """
    c = (claim_text or "").lower()
    r = (ref_snippet or "").lower()

    c_has_affirm = any(w in c for w in AFFIRM_CUES)
    c_has_neg = any(w in c for w in NEGATION_CUES)
    r_has_affirm = any(w in r for w in AFFIRM_CUES)
    r_has_neg = any(w in r for w in NEGATION_CUES)

    # Simple cases
    if c_has_affirm and r_has_neg:
        return "conflict"
    if c_has_neg and r_has_affirm:
        return "conflict"
    if (c_has_affirm and r_has_affirm) or (c_has_neg and r_has_neg):
        return "support"
    return "neutral"

def citation_confidence_metrics(claim_text: str, references: list):
    """
    Step 4.2:
    - citation_confidence: 0-100 based on match_strength + convergence
    - convergence_score: number of supporting refs (weighted)
    - conflict_index: number of conflicting refs (weighted)
    """
    if not references:
        return {
            "citation_confidence": 0,
            "convergence_score": 0,
            "conflict_index": 0,
            "supporting_refs": 0,
            "conflicting_refs": 0
        }

    support = 0.0
    conflict = 0.0
    neutral = 0.0

    for r in references:
        # Use match_strength as weight proxy
        w = float(r.get("match_strength", 0.0))
        label = detect_support_or_conflict(claim_text, r.get("snippet", ""))
        if label == "support":
            support += (1.0 + w)
        elif label == "conflict":
            conflict += (1.0 + w)
        else:
            neutral += (0.5 + w)

    # Confidence: reward support and multi-source convergence; penalize conflict
    raw = (support * 22) + (neutral * 10) - (conflict * 28)
    # Convergence bonus: more than 1 supporting ref increases trust
    raw += max(0.0, (support - 1.0) * 8)

    citation_conf = int(max(0, min(100, round(raw))))
    return {
        "citation_confidence": citation_conf,
        "convergence_score": round(support, 2),
        "conflict_index": round(conflict, 2),
        "supporting_refs": int(round(support)),
        "conflicting_refs": int(round(conflict))
    }

def governance_flags_for_claim(metrics: dict):
    """
    Governance flags are what acquirers care about:
    - "LOW_EVIDENCE" if no refs or low citation confidence
    - "CONFLICTING_SOURCES" if conflict_index is meaningful
    - "SINGLE_SOURCE" if only 1 supporting ref
    """
    flags = []
    cc = metrics.get("citation_confidence", 0)
    conv = metrics.get("convergence_score", 0)
    conf = metrics.get("conflict_index", 0)

    if cc < 35:
        flags.append("LOW_EVIDENCE")
    if conf >= 1.0:
        flags.append("CONFLICTING_SOURCES")
    if conv > 0 and metrics.get("supporting_refs", 0) <= 1:
        flags.append("SINGLE_SOURCE")
    if not flags:
        flags.append("OK")
    return flags

def overall_score(claims_payload: list):
    """
    Aggregate to a TruCite truth_score (0-100):
    - Use claim weight * citation_confidence
    - Penalize conflict
    """
    if not claims_payload:
        return 0, "Insufficient Input"

    total_w = 0.0
    total = 0.0
    total_conflict = 0.0

    for c in claims_payload:
        w = float(c.get("confidence_weight", 1.0))
        cc = float(c.get("citation_confidence", 0))
        conf = float(c.get("conflict_index", 0))
        total_w += w
        total += w * cc
        total_conflict += conf

    base = total / max(1e-6, total_w)

    # conflict penalty
    base -= min(25.0, total_conflict * 8.0)

    score = int(max(0, min(100, round(base))))

    if score >= 80:
        verdict = "High Confidence"
    elif score >= 60:
        verdict = "Plausible / Needs Verification"
    elif score >= 40:
        verdict = "Questionable / High Uncertainty"
    else:
        verdict = "Likely False / Very High Uncertainty"

    return score, verdict

def stable_event_id(text: str):
    h = hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
    return f"evt_{h}"

# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": UTC_NOW(),
        "routes": ["/", "/health", "/verify"]
    })

@app.get("/")
def root():
    # HTML fingerprint page; not the marketing landing page (that stays on the frontend)
    html = f"""
    <!doctype html>
    <html>
      <head>
        <meta charset="utf-8"/>
        <meta name="viewport" content="width=device-width, initial-scale=1"/>
        <title>TruCite Backend</title>
        <style>
          body {{
            background: #070707;
            color: #f6d365;
            font-family: Arial, sans-serif;
            padding: 28px;
          }}
          .box {{
            max-width: 720px;
            border: 1px solid rgba(246, 211, 101, 0.35);
            border-radius: 14px;
            padding: 20px;
            line-height: 1.5;
          }}
          .muted {{
            color: rgba(246, 211, 101, 0.75);
          }}
        </style>
      </head>
      <body>
        <div class="box">
          <h2>TruCite Backend HTML is Live</h2>
          <p><b>FINGERPRINT:</b> {FINGERPRINT}</p>
          <p><b>UTC:</b> {UTC_NOW()}</p>
          <p class="muted">
            This endpoint is intentionally not the marketing landing page. Use your frontend (Neocities) for UI.
            API endpoints: <code>/health</code> and <code>/verify</code>.
          </p>
        </div>
      </body>
    </html>
    """
    return Response(html, mimetype="text/html")

@app.post("/verify")
def verify():
    payload = request.get_json(silent=True) or {}
    text = normalize_text(payload.get("text", ""))

    if not text:
        return jsonify({
            "error": "Missing 'text' in request body",
            "example": {"text": "The moon is made of candy. Humans landed on the moon in 1969."}
        }), 400

    # Claim Engine v2
    claims = extract_claims(text)

    enriched_claims = []
    for c in claims:
        refs = match_seed_references(c["text"], top_k=3)

        # Step 4.2 metrics
        metrics = citation_confidence_metrics(c["text"], refs)
        flags = governance_flags_for_claim(metrics)

        enriched = {
            **c,
            "references": refs,
            **metrics,
            "governance_flags": flags
        }
        enriched_claims.append(enriched)

    score, verdict = overall_score(enriched_claims)

    # "confidence_curve" placeholder (Step 6.x adds drift history + integrity chain)
    confidence_curve = [{
        "time_utc": UTC_NOW(),
        "truth_score": score
    }]

    # Governance at response-level (MVP)
    governance_flags = []
    if score < 60:
        governance_flags.append("REVIEW_RECOMMENDED")
    if any("CONFLICTING_SOURCES" in c.get("governance_flags", []) for c in enriched_claims):
        governance_flags.append("CONFLICT_PRESENT")
    if any("LOW_EVIDENCE" in c.get("governance_flags", []) for c in enriched_claims):
        governance_flags.append("LOW_EVIDENCE_PRESENT")
    if not governance_flags:
        governance_flags.append("OK")

    return jsonify({
        "mode": "claim_engine_v2_step_4_2",
        "event_id": stable_event_id(text),
        "input": text,
        "claims": enriched_claims,
        "final_score": score,
        "verdict": verdict,
        "confidence_curve": confidence_curve,
        "governance_flags": governance_flags,
        "explanation": "Claim Engine v2 + MVP Reference Grounding (seed corpus) + Step 4.2 Citation Confidence & Convergence/Conflict metrics. Next: drift tracking + integrity chain."
    })

# Render expects "app:app" for gunicorn
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
