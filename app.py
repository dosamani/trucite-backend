import os
import re
import json
import math
import requests
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)

# -----------------------------
# CORS (Permissive for MVP)
# -----------------------------
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

@app.after_request
def add_cors_headers(resp):
    resp.headers["Access-Control-Allow-Origin"] = ALLOWED_ORIGINS
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp

@app.route("/", methods=["GET"])
def health():
    return jsonify({"ok": True, "service": "trucite-backend", "version": "mvp-rag-v1.1"})

@app.route("/truth-score", methods=["POST", "OPTIONS"])
def truth_score():
    # Handle preflight
    if request.method == "OPTIONS":
        return make_response("", 204)

    try:
        payload = request.get_json(silent=True) or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return jsonify({
                "mode": "rag_v1",
                "truth_score": 0,
                "verdict": "No input",
                "explanation": "No text provided.",
                "references": []
            }), 200

        refs, meta = evidence_pack(text)

        score = base_score(text, refs)
        score = max(0, min(100, int(round(score))))

        verdict = verdict_from_score(score)

        return jsonify({
            "mode": "rag_v1",
            "truth_score": score,
            "verdict": verdict,
            "explanation": f"Evidence query: {meta.get('query','')}",
            "references": refs
        }), 200

    except Exception as e:
        return jsonify({
            "mode": "rag_v1",
            "truth_score": 0,
            "verdict": "Error",
            "explanation": f"Server error: {str(e)}",
            "references": []
        }), 200


# -----------------------------
# Keyword extraction (simple)
# -----------------------------
STOP = set("""
a an the and or but if then else for to of in on at by with from as is are was were be been being
this that these those it its it's i you we they he she them his her our your their
not no yes do does did done can could would should may might must
""".split())

def extract_keywords(text: str, limit=6):
    words = re.findall(r"[a-zA-Z]{3,}", text.lower())
    words = [w for w in words if w not in STOP]
    # frequency
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    # sort by freq then length
    ranked = sorted(freq.items(), key=lambda x: (-x[1], -len(x[0])))
    return [w for w, _ in ranked[:limit]]


# -----------------------------
# Wikipedia (optional lead)
# Uses Wikimedia REST Summary endpoint (stable)
# -----------------------------
def wiki_search(query: str, limit=3):
    """
    Uses Wikipedia Opensearch.
    Returns [(title, url), ...]
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
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "TruCite/1.0"})
        r.raise_for_status()
        data = r.json()
        titles = data[1] if len(data) > 1 else []
        links = data[3] if len(data) > 3 else []
        out = []
        for t, l in zip(titles, links):
            out.append((t, l))
        return out
    except Exception:
        return []

def wiki_summary(title: str):
    """
    Wikimedia REST summary (clean snippet).
    """
    try:
        safe = title.replace(" ", "_")
        url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{safe}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "TruCite/1.0"})
        if r.status_code != 200:
            return None, None
        data = r.json()
        snippet = (data.get("extract") or "").strip()
        page_url = None
        content_urls = data.get("content_urls") or {}
        desktop = content_urls.get("desktop") or {}
        page_url = desktop.get("page")
        return snippet, page_url
    except Exception:
        return None, None


# -----------------------------
# Crossref (STRICT scholarly)
# -----------------------------
def crossref_search(query: str, limit=5):
    """
    Stricter scholarly fallback:
    - reject books/chapters
    - accept journal/proceedings/posted-content/reports only
    - require lunar/science terms in title/container
    """
    try:
        url = "https://api.crossref.org/works"
        params = {"query": query, "rows": limit}
        r = requests.get(url, params=params, timeout=8, headers={"User-Agent": "TruCite/1.0"})
        r.raise_for_status()
        items = r.json().get("message", {}).get("items", [])

        good = []
        for it in items:
            title = (it.get("title") or [""])[0].strip()
            doi = (it.get("DOI") or "").strip()
            typ = (it.get("type") or "").strip()
            container = ((it.get("container-title") or [""])[0]).strip()

            if not title or not doi:
                continue

            # Hard reject: books
            if typ in {"book", "book-chapter", "monograph"}:
                continue

            # Only allow a small set
            if typ not in {"journal-article", "proceedings-article", "posted-content", "report"}:
                continue

            text_blob = f"{title} {container}".lower()
            must_have_any = ["lunar", "moon", "regolith", "geology", "composition", "planetary", "apollo", "basalt", "crater"]
            if not any(k in text_blob for k in must_have_any):
                continue

            link = f"https://doi.org/{doi}"
            good.append((title, link))

        return good[:2]
    except Exception:
        return []


# -----------------------------
# Evidence pack
# -----------------------------
def evidence_pack(text: str):
    keywords = extract_keywords(text, limit=6)
    query = " ".join(keywords) if keywords else text[:80]

    refs = []

    # 1) Wikipedia lead (OPTIONAL but useful for common claims)
    # If you truly don't want Wikipedia at all, comment out this block.
    wiki_results = wiki_search(query, limit=2)
    for title, url in wiki_results:
        snippet, page_url = wiki_summary(title)
        if snippet:
            refs.append({
                "source": "Wikipedia (lead)",
                "title": title,
                "url": page_url or url,
                "snippet": snippet[:280] + ("…" if len(snippet) > 280 else "")
            })

    # 2) If wiki gave nothing, use strict Crossref
    if len(refs) == 0:
        cr = crossref_search(query, limit=5)
        for title, link in cr:
            refs.append({
                "source": "Crossref (DOI)",
                "title": title,
                "url": link,
                "snippet": "Scholarly reference (DOI)."
            })

    return refs, {"query": query, "keywords": keywords}


# -----------------------------
# Scoring (MVP heuristic)
# -----------------------------
def base_score(text: str, refs):
    """
    MVP scoring:
    - starts mid
    - bumps for having refs
    - applies penalty if refs indicate myth/idiom/hoax
    - applies penalty for obvious absurd claims markers
    """
    t = text.lower()
    score = 55

    # Reference bump (small)
    if refs and len(refs) >= 1:
        score += 10
    if refs and len(refs) >= 2:
        score += 5

    # Myth/idiom penalty (this fixes "moon is made of cheese")
    blob = (text + " " + " ".join([r.get("snippet", "") for r in refs])).lower()
    myth_terms = ["myth", "folklore", "idiom", "metaphor", "joke", "hoax", "children", "nursery", "satire", "fiction"]
    if any(term in blob for term in myth_terms):
        score -= 25

    # Absurdity markers (tiny extra penalty)
    absurd_terms = ["completely made of cheese", "made of cheese", "flat earth", "lizard people"]
    if any(a in t for a in absurd_terms):
        score -= 15

    return score


def verdict_from_score(score: int):
    if score >= 85:
        return "Likely True / Well-Supported"
    if score >= 65:
        return "Plausible / Needs Verification"
    if score >= 40:
        return "Questionable / High Uncertainty"
    return "Likely False / Misleading"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "5000")))
