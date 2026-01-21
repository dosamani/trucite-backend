# app.py — TruCite backend (MVP)
# FastAPI + static landing page + /verify scoring endpoint
# Render will serve:
#   GET  /         -> static/index.html (landing page)
#   GET  /static/* -> static assets
#   POST /verify   -> scoring JSON

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import re

# Optional claim parser (do NOT fail deploy if missing or signature differs)
extract_claims = None
try:
    from claim_parser import extract_claims as _extract_claims  # type: ignore
    extract_claims = _extract_claims
except Exception:
    extract_claims = None

app = FastAPI(title="TruCite Engine", version="0.3.4")

# CORS (permissive for MVP demo)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Static site hosting
# ----------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    index_path = STATIC_DIR / "index.html"
    if not index_path.exists():
        return HTMLResponse(
            content=(
                "<h2>TruCite backend is running</h2>"
                "<p><strong>Missing static/index.html</strong> in repo.</p>"
                "<p>Expected path: <code>static/index.html</code></p>"
            ),
            status_code=200,
        )
    return HTMLResponse(content=index_path.read_text(encoding="utf-8"), status_code=200)

@app.get("/health")
def health():
    return {"status": "ok", "service": "TruCite Backend", "ts_utc": datetime.now(timezone.utc).isoformat()}

# ----------------------------
# Models
# ----------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs pasted from the evidence box

# ----------------------------
# Helpers: signals + scoring
# ----------------------------
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)

# PubMed URL + PMID number patterns
PUBMED_URL_RE = re.compile(r"https?://(www\.)?pubmed\.ncbi\.nlm\.nih\.gov/\d+/?", re.IGNORECASE)
PMID_TOKEN_RE = re.compile(r"\bPMID\s*:\s*(\d{6,9})\b|\bPMID\s+(\d{6,9})\b", re.IGNORECASE)
PMID_BARE_RE = re.compile(r"\b(\d{6,9})\b")  # fallback: bare 6-9 digits

ABSOLUTE_WORDS = [
    "always", "never", "guaranteed", "proven", "definitely", "certainly", "no doubt",
    "cannot", "will", "must", "only", "everyone", "nobody"
]

STOPWORDS = {
    "a","an","the","and","or","but","if","then","else","for","to","of","in","on","at","by","with",
    "is","are","was","were","be","been","being","as","it","this","that","these","those","from",
    "into","over","under","about","we","you","they","i","he","she","them","our","your","their"
}

def build_audit_fingerprint(payload: str) -> Dict[str, str]:
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"sha256": sha, "timestamp_utc": datetime.now(timezone.utc).isoformat()}

def compute_signals(text: str) -> Dict[str, Any]:
    t = text.strip()
    has_url = bool(URL_RE.search(t))
    has_doi = bool(DOI_RE.search(t))
    has_pubmed_url = bool(PUBMED_URL_RE.search(t))
    has_pmid_token = bool(PMID_TOKEN_RE.search(t))
    has_year = bool(re.search(r"\b(19|20)\d{2}\b", t))
    has_percent = "%" in t
    numerics = re.findall(r"\d+(\.\d+)?", t)
    numeric_count = len(numerics)
    has_numerics = numeric_count > 0

    absolute_count = 0
    lower = t.lower()
    for w in ABSOLUTE_WORDS:
        absolute_count += lower.count(w)

    has_citation_like = bool(re.search(r"\[\d+\]|\(\s*(19|20)\d{2}\s*\)", t)) or (has_year and has_percent)

    return {
        "has_url": has_url,
        "has_doi": has_doi,
        "has_pubmed_url": has_pubmed_url,
        "has_pmid": has_pmid_token,  # claim itself includes PMID token
        "has_year": has_year,
        "has_percent": has_percent,
        "has_numerics": has_numerics,
        "numeric_count": numeric_count,
        "absolute_count": absolute_count,
        "has_citation_like": has_citation_like,
    }

def infer_claim_type(text: str, sig: Dict[str, Any]) -> str:
    lower = text.lower()

    if sig.get("has_percent") or sig.get("numeric_count", 0) >= 2:
        return "numeric_or_stat_claim"

    med_words = ["aspirin", "mi", "myocardial", "stroke", "mortality", "risk", "trial", "meta-analysis", "guideline"]
    if any(w in lower for w in med_words):
        return "medical_claim"

    fin_words = ["roi", "yield", "interest rate", "stock", "bond", "inflation"]
    if any(w in lower for w in fin_words):
        return "finance_claim"

    legal_words = ["illegal", "compliance", "statute", "regulation", "liable", "lawsuit"]
    if any(w in lower for w in legal_words):
        return "legal_claim"

    return "general_claim"

def tokenize(s: str) -> List[str]:
    s = re.sub(r"[^a-z0-9\s%]", " ", s.lower())
    parts = [p for p in s.split() if p and p not in STOPWORDS and len(p) > 2]
    return parts

def extract_evidence_signals(evidence_text: Optional[str]) -> Dict[str, Any]:
    if not evidence_text:
        return {"provided": False, "has_url": False, "has_doi": False, "has_pmid": False, "has_pubmed_url": False, "pmids": []}

    e = evidence_text.strip()
    has_url = bool(URL_RE.search(e))
    has_doi = bool(DOI_RE.search(e))
    has_pubmed_url = bool(PUBMED_URL_RE.search(e))

    pmids: List[str] = []
    for m in PMID_TOKEN_RE.finditer(e):
        pmids.extend([x for x in m.groups() if x])

    # If PubMed URL exists, extract the numeric id in the URL as PMID-like
    if has_pubmed_url:
        for m in re.finditer(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d+)", e, re.IGNORECASE):
            pmids.append(m.group(1))

    # Fallback: if user pastes a bare PMID number only (6-9 digits)
    if not pmids:
        for m in PMID_BARE_RE.finditer(e):
            pmids.append(m.group(1))

    # de-dup
    pmids = list(dict.fromkeys(pmids))

    has_pmid = len(pmids) > 0

    return {
        "provided": bool(e),
        "has_url": has_url,
        "has_doi": has_doi,
        "has_pmid": has_pmid,
        "has_pubmed_url": has_pubmed_url,
        "pmids": pmids,
    }

def evidence_validated_mvp(claim_text: str, evidence_text: Optional[str]) -> Dict[str, Any]:
    """
    MVP relevance heuristic:
    - Evidence must include at least one: URL/DOI/PMID
    - AND must have minimal topical overlap with claim tokens.
    This is NOT true validation; it’s a sanity check to prevent random PubMed links boosting anything.
    """
    ev = extract_evidence_signals(evidence_text)
    if not ev["provided"]:
        return {"validated": False, "reason": "no_evidence"}

    if not (ev["has_url"] or ev["has_doi"] or ev["has_pmid"]):
        return {"validated": False, "reason": "no_recognized_evidence_markers"}

    claim_tokens = set(tokenize(claim_text))
    evidence_tokens = set(tokenize(evidence_text or ""))

    overlap = claim_tokens.intersection(evidence_tokens)
    # allow a small set of medical keywords to pass if evidence includes them
    min_overlap = 1
    validated = len(overlap) >= min_overlap

    return {
        "validated": bool(validated),
        "reason": "keyword_overlap" if validated else "no_overlap",
        "overlap_terms": sorted(list(overlap))[:8]
    }

def implausibility_caps(text: str) -> Dict[str, Any]:
    lower = text.lower()
    tags = []
    cap = None

    if "moon" in lower and ("1km" in lower or "1 km" in lower or "made up of candy" in lower or "made of candy" in lower):
        tags.append("world_knowledge_red_flag")
        cap = 25

    if "made up of candy" in lower or "made of candy" in lower:
        tags.append("absurdity_red_flag")
        cap = 20 if cap is None else min(cap, 20)

    return {"cap": cap, "tags": tags}

def verdict_from_score(score: int) -> str:
    if score <= 55:
        return "High risk / do not rely"
    if score <= 75:
        return "Unclear / needs verification"
    return "Likely reliable (still verify for high-stakes use)"

def score_one_claim(claim_text: str, evidence_text: Optional[str]) -> Dict[str, Any]:
    sig = compute_signals(claim_text)
    claim_type = infer_claim_type(claim_text, sig)

    # conservative baseline
    score = 78

    # penalize absolutist language
    score -= min(sig.get("absolute_count", 0) * 3, 12)

    # evidence evaluation
    ev = extract_evidence_signals(evidence_text)
    ev_val = evidence_validated_mvp(claim_text, evidence_text)
    evidence_present = bool(ev["provided"]) and (ev["has_url"] or ev["has_doi"] or ev["has_pmid"])
    evidence_validated = bool(ev_val["validated"])

    # high-liability categories require validated evidence to exceed cap
    needs_evidence = claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim")

    risk_tags: List[str] = []

    if needs_evidence:
        if not evidence_present:
            risk_tags.append("citation_unverified")
            if claim_type == "numeric_or_stat_claim":
                risk_tags.append("numeric_claim")
            # hard cap without evidence
            score = min(score, 55)
        else:
            # evidence present but not validated: do NOT boost; still cap conservatively
            if not evidence_validated:
                risk_tags.append("evidence_unvalidated")
                score = min(score, 75)  # ensures you won't see >75 in this state
            else:
                # validated evidence: modest boost, but still not "perfect"
                score = min(90, score + 8)

    # implausibility caps override everything
    imp = implausibility_caps(claim_text)
    if imp["cap"] is not None:
        score = min(score, int(imp["cap"]))
        risk_tags.extend(imp["tags"])

    score = max(0, min(100, int(score)))
    verdict = verdict_from_score(score)

    evidence_needed = None
    if "citation_unverified" in risk_tags:
        evidence_needed = {
            "required": True,
            "reason": "Claim includes numeric/statistical or high-liability content without attached evidence (URL/DOI/PMID).",
            "acceptable_evidence_examples": [
                "Peer-reviewed paper link (DOI/PMID/URL)",
                "Clinical guideline link (society guideline URL)",
                "Regulatory label / official statement URL",
            ],
            "suggested_query": f"{claim_text} PMID",
        }
    elif "evidence_unvalidated" in risk_tags:
        evidence_needed = {
            "required": True,
            "reason": "Evidence was provided but appears unrelated to the claim (MVP relevance check). Provide a matching DOI/PMID/URL for this specific claim.",
            "acceptable_evidence_examples": [
                "PubMed link for the specific claim",
                "DOI for the cited trial or meta-analysis",
                "Guideline URL directly supporting the claim",
            ],
            "suggested_query": f"{claim_text} clinical trial meta-analysis PMID",
        }

    return {
        "text": claim_text,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": list(dict.fromkeys(risk_tags)),
        "score": score,
        "verdict": verdict,
        "evidence_needed": evidence_needed,
        "evidence_relevance_mvp": {
            "validated": evidence_validated,
            "reason": ev_val.get("reason"),
            "overlap_terms": ev_val.get("overlap_terms", []),
        }
    }

# ----------------------------
# API
# ----------------------------
@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text'")

    # extract claims if claim_parser exists; otherwise treat whole text as one claim
    claims: List[Dict[str, Any]] = []
    if extract_claims:
        try:
            parsed = extract_claims(text)
            if isinstance(parsed, list) and parsed:
                for c in parsed:
                    if isinstance(c, dict) and "text" in c:
                        claims.append({"text": str(c.get("text", "")).strip()})
                    elif isinstance(c, str):
                        claims.append({"text": c.strip()})
        except Exception:
            claims = []

    if not claims:
        claims = [{"text": text}]

    scored_claims: List[Dict[str, Any]] = []
    claim_scores: List[int] = []

    for c in claims:
        claim_text = str(c.get("text", "")).strip() or text
        scored = score_one_claim(claim_text, req.evidence)
        scored_claims.append(scored)
        claim_scores.append(int(scored["score"]))

    overall_score = min(claim_scores) if claim_scores else 55
    overall_verdict = verdict_from_score(overall_score)

    payload_for_fingerprint = f"{text}|{req.evidence or ''}|{overall_score}|{overall_verdict}"
    audit_fp = build_audit_fingerprint(payload_for_fingerprint)

    ev = extract_evidence_signals(req.evidence)

    return {
        "audit_fingerprint": audit_fp,
        "event_id": audit_fp["sha256"][:12],
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
        },
        "score": overall_score,
        "verdict": overall_verdict,
        "claims": scored_claims,
        "explanation": (
            "MVP heuristic verification. Evidence is detected but not validated. "
            "High-liability claims cannot reach 'Likely reliable' without validated evidence relevance. "
            "Enterprise mode performs true evidence validation, drift analytics, and policy controls."
        ),
        "evidence": {
            "provided": bool(ev.get("provided")),
            "signals": {
                "has_url": bool(ev.get("has_url")),
                "has_doi": bool(ev.get("has_doi")),
                "has_pmid": bool(ev.get("has_pmid")),
                "has_pubmed_url": bool(ev.get("has_pubmed_url")),
                "pmids": ev.get("pmids", []),
            }
        }
    }
