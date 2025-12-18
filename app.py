import os
import re
import json
import time
import sqlite3
import hashlib
from datetime import datetime
from urllib.parse import quote

import requests
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

# ==========================================
# TruCite Backend (RAG-ish Verification v1)
# ==========================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
DB_PATH = os.path.join(BASE_DIR, "trucite.sqlite")

app = Flask(__name__, static_folder="static", static_url_path="")
CORS(app, resources={r"/*": {"origins": "*"}}, supports_credentials=False)

# ----------------------------
# Static frontend hosting
# ----------------------------
@app.get("/")
def serve_index():
    return send_from_directory(STATIC_DIR, "index.html")

@app.get("/<path:filename>")
def serve_static(filename):
    return send_from_directory(STATIC_DIR, filename)

@app.get("/health")
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "ts": int(time.time())})

# ----------------------------
# DB / drift setup
# ----------------------------
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        input_preview TEXT NOT NULL,
        score INTEGER NOT NULL,
        verdict TEXT NOT NULL,
        explanation TEXT NOT NULL,
        references_json TEXT NOT NULL,
        signals_json TEXT NOT NULL
      )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_runs_hash ON runs(input_hash)")
    conn.commit()
    conn.close()

init_db()

def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

def now_iso() -> str:
    return datetime.utcnow().isoformat() + "Z"

# ----------------------------
# Simple text signals
# ----------------------------
HEDGE_WORDS = {"may", "might", "could", "possibly", "likely", "appears", "suggests", "approximately"}
ABSOLUTE_WORDS = {"always", "never", "guaranteed", "proven", "undeniable", "100%", "certain"}

def extract_numbers(text: str):
    # captures integers, decimals, percentages
    return re.findall(r"\b\d+(?:\.\d+)?%?\b", text)

def extract_keywords(text: str, limit=8):
    # super-light keyword extraction (not NLP-heavy)
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", " ", text.lower())
    words = [w for w in cleaned.split() if len(w) >= 4]
    stop = set([
        "this","that","with","from","have","will","they","them","there","their","what","when",
        "where","which","would","could","should","about","because","into","your","just","than",
        "also","very","more","most","some","many","such","only","been","being","over","under",
        "then","here","like","make","made","does","done","even","true","false"
    ])
    words = [w for w in words if w not in stop]
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w,_ in ranked[:limit]]

def detect_tone(text: str):
    lower = text.lower()
    hedge = sum(1 for w in HEDGE_WORDS if w in lower)
    absolute = sum(1 for w in ABSOLUTE_WORDS if w in lower)
    return {"hedge_hits": hedge, "absolute_hits": absolute}

# ----------------------------
# Retrieval helpers (RAG-ish)
# ----------------------------
def wiki_search(query: str, limit=3):
    """
    Uses Wikipedia Opensearch API
    returns list of (title, url)
    """
    try:
        url = "https://en.wikipedia.org/w/api.php"
        params = {
            "action": "opensearch",
            "search": query,
            "limit": limit,
            "namespace": 0,
            "format": "json"
        }
        r = requests.get(url, params=params, timeout=8)
        r.raise_for_status()
        data = r.json()
        titles = data[1] if len(data) > 1 else []
        urls = data[3] if len(data) > 3 else []
        return list(zip(titles, urls))
    except Exception:
        return []

def wiki_summary(title: str):
    """
    Uses REST summary endpoint
    """
    try:
        safe = quote(title.replace(" ", "_"))
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
        r = requests.get(url, timeout=8)
        r.raise_for_status()
        j = r.json()
        extract = (j.get("extract") or "").strip()
        page = (j.get("content_urls", {}).get("desktop", {}).get("page")) or ""
        return extract, page
    except Exception:
        return "", ""

def crossref_search(query: str, limit=2):
    """
    Basic scholarly fallback (not perfect, but gives credible refs)
    """
    try:
        url = "https://api.crossref.org/works"
        params = {"query": query, "rows": limit}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "TruCite/1.0"})
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])
        out = []
        for it in items:
            title = (it.get("title") or [""])[0]
            doi = it.get("DOI") or ""
            link = f"https://doi.org/{doi}" if doi else ""
            out.append((title, link))
        return out
    except Exception:
        return []

def evidence_pack(text: str):
    """
    Returns references list:
    [{source, title, url, snippet}]
    """
    keywords = extract_keywords(text, limit=6)
    query = " ".join(keywords) if keywords else text[:80]

    refs = []

    # 1) Wikipedia search + summaries
    results = wiki_search(query, limit=3)
    for title, url in results:
        snippet, page_url = wiki_summary(title)
        if snippet:
            refs.append({
                "source": "Wikipedia",
                "title": title,
                "url": page_url or url,
                "snippet": snippet[:300] + ("…" if len(snippet) > 300 else "")
            })

    # 2) Crossref fallback (credible citations even if snippet not available)
    if len(refs) < 2:
        cr = crossref_search(query, limit=2)
        for title, link in cr:
            if title and link:
                refs.append({
                    "source": "Crossref",
                    "title": title,
                    "url": link,
                    "snippet": "Scholarly reference (DOI)."
                })

    return refs, {"query": query, "keywords": keywords}

# ----------------------------
# Support scoring (v1 heuristic)
# ----------------------------
def compute_support_score(text: str, refs):
    """
    Simple scoring: does the input share meaningful keywords with evidence snippets?
    """
    kw = set(extract_keywords(text, limit=10))
    if not kw:
        return 45, {"support_hits": 0, "support_ratio": 0.0}

    combined = " ".join([(r.get("snippet","") + " " + r.get("title","")) for r in refs]).lower()
    hits = sum(1 for k in kw if k in combined)
    ratio = hits / max(1, len(kw))

    # base score from overlap
    base = 35 + int(ratio * 55)  # 35..90

    # tone adjustment
    tone = detect_tone(text)
    if tone["absolute_hits"] > 0 and ratio < 0.30:
        base -= 10  # penalize absolute claims with low support
    if tone["hedge_hits"] > 0 and ratio < 0.30:
        base += 3   # small bump for uncertainty language

    # numeric adjustment
    nums = extract_numbers(text)
    if nums and ratio < 0.25:
        base -= 5  # numbers without supporting evidence tend to be risky

    score = max(0, min(100, base))
    signals = {
        "keywords": sorted(list(kw)),
        "support_hits": hits,
        "support_ratio": round(ratio, 3),
        "tone": tone,
        "numbers": nums
    }
    return score, signals

def verdict_from_score(score: int):
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"

# ----------------------------
# Drift tracking
# ----------------------------
def get_last_run(input_hash: str):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
      SELECT created_at, score, verdict
      FROM runs
      WHERE input_hash = ?
      ORDER BY id DESC
      LIMIT 1
    """, (input_hash,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return {"created_at": row[0], "score": row[1], "verdict": row[2]}

def save_run(input_hash: str, preview: str, score: int, verdict: str, explanation: str, refs, signals):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
      INSERT INTO runs
      (created_at, input_hash, input_preview, score, verdict, explanation, references_json, signals_json)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_iso(),
        input_hash,
        preview,
        int(score),
        verdict,
        explanation,
        json.dumps(refs, ensure_ascii=False),
        json.dumps(signals, ensure_ascii=False)
    ))
    conn.commit()
    conn.close()

# ----------------------------
# API: /truth-score
# ----------------------------
@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    if request.method == "OPTIONS":
        return ("", 204)

    payload = request.get_json(silent=True) or {}
    text = (payload.get("text") or "").strip()
    if not text:
        return jsonify({"error": "Missing 'text' in JSON payload."}), 400

    h = sha256(text)
    preview = (text[:140] + "…") if len(text) > 140 else text

    last = get_last_run(h)

    refs, retrieval = evidence_pack(text)
    score, signals = compute_support_score(text, refs)
    verdict = verdict_from_score(score)

    explanation_parts = []
    explanation_parts.append(f"Evidence query: {retrieval.get('query','')}")
    explanation_parts.append(f"Support overlap: {signals.get('support_hits',0)}/{len(signals.get('keywords',[]))} keywords matched evidence.")
    if signals.get("numbers"):
        explanation_parts.append(f"Numbers detected: {', '.join(signals['numbers'][:6])}" + ("…" if len(signals['numbers']) > 6 else ""))
    if signals.get("tone", {}).get("absolute_hits", 0) > 0:
        explanation_parts.append("Contains absolute-language claims; penalized if evidence support is weak.")
    explanation = " ".join(explanation_parts)

    # drift object
    drift = {"has_prior": False}
    if last:
        drift["has_prior"] = True
        drift["previous"] = last
        drift["delta_score"] = int(score) - int(last["score"])

    save_run(h, preview, score, verdict, explanation, refs, signals)

    return jsonify({
        "mode": "rag_v1",
        "truth_score": int(score),
        "verdict": verdict,
        "explanation": explanation,
        "references": refs,
        "signals": signals,
        "drift": drift
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
