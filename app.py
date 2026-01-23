# app.py — TruCite backend (Decision-Gated MVP + evidence relevance)
# FastAPI + static landing page + /verify scoring endpoint

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

# ---------------------------------------
# Optional claim parser (safe import)
# ---------------------------------------
extract_claims = None
try:
    from claim_parser import extract_claims as _extract_claims  # type: ignore
    extract_claims = _extract_claims
except Exception:
    extract_claims = None


app = FastAPI(title="TruCite Engine", version="0.4.1")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------
# Static hosting
# ---------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.get("/", response_class=HTMLResponse)
def home():
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return HTMLResponse("<h3>TruCite backend running. Missing index.html.</h3>")
    return HTMLResponse(index.read_text(encoding="utf-8"))


# ---------------------------------------
# Models
# ---------------------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None
    policy_mode: Optional[str] = "enterprise"


# ---------------------------------------
# Regex + constants
# ---------------------------------------
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
PUBMED_URL_RE = re.compile(r"https?://pubmed\.ncbi\.nlm\.nih\.gov/(\d{6,9})/?", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PMID_TOKEN_RE = re.compile(r"\bPMID[:\s]*([0-9]{6,9})\b", re.IGNORECASE)

ABSOLUTE_WORDS = [
    "always", "never", "guaranteed", "proven", "certain", "definitely",
    "cannot", "must", "everyone", "nobody"
]

STOPWORDS = {
    "a", "an", "the", "and", "or", "to", "of", "in", "on", "for", "with", "by",
    "is", "are", "was", "were", "be", "been", "being", "this", "that", "these",
    "those", "it", "its", "as", "at", "from", "into", "over", "under", "than",
    "then", "but", "if", "while", "during", "after", "before", "about"
}


# ---------------------------------------
# Helpers
# ---------------------------------------
def compute_signals(text: str) -> Dict[str, Any]:
    lower = text.lower()
    numerics = re.findall(r"\d+(\.\d+)?", text)

    return {
        "has_url": bool(URL_RE.search(text)),
        "has_doi": bool(DOI_RE.search(text)),
        "has_pubmed_url": bool(PUBMED_URL_RE.search(text)),
        "has_pmid": bool(PMID_TOKEN_RE.search(text)) or bool(PUBMED_URL_RE.search(text)),
        "has_year": bool(re.search(r"\b(19|20)\d{2}\b", text)),
        "has_percent": "%" in text,
        "has_numerics": bool(numerics),
        "numeric_count": len(numerics),
        "absolute_count": sum(lower.count(w) for w in ABSOLUTE_WORDS),
        "has_citation_like": bool(re.search(r"\(\d{4}\)|\[\d+\]", text)),
    }


def infer_claim_type(text: str, sig: Dict[str, Any]) -> str:
    lower = text.lower()
    if sig["has_percent"] or sig["numeric_count"] >= 2:
        return "numeric_or_stat_claim"
    if any(w in lower for w in ["aspirin", "mi", "myocardial", "infarction", "stroke", "risk", "mortality"]):
        return "medical_claim"
    if any(w in lower for w in ["roi", "interest", "yield", "inflation", "bond", "stock"]):
        return "finance_claim"
    if any(w in lower for w in ["law", "illegal", "liable", "statute", "regulation", "compliance"]):
        return "legal_claim"
    return "general_claim"


def _extract_pmids(text: str) -> List[str]:
    if not text:
        return []
    pmids = []

    for m in PMID_TOKEN_RE.finditer(text):
        pmids.append(m.group(1))

    for m in PUBMED_URL_RE.finditer(text):
        pmids.append(m.group(1))

    # de-dupe preserve order
    seen = set()
    out = []
    for p in pmids:
        if p not in seen:
            out.append(p)
            seen.add(p)
    return out


def detect_evidence(text: Optional[str]) -> Dict[str, Any]:
    if not text or not text.strip():
        return {
            "provided": False,
            "raw": "",
            "has_url": False,
            "has_doi": False,
            "has_pmid": False,
            "has_pubmed_url": False,
            "pmids": [],
        }

    raw = text.strip()
    pmids = _extract_pmids(raw)

    return {
        "provided": True,
        "raw": raw,
        "has_url": bool(URL_RE.search(raw)),
        "has_doi": bool(DOI_RE.search(raw)),
        "has_pubmed_url": bool(PUBMED_URL_RE.search(raw)),
        "has_pmid": bool(pmids),
        "pmids": pmids,
    }


def implausibility_check(text: str) -> Dict[str, Any]:
    lower = text.lower()
    tags = []

    if "moon" in lower and ("1km" in lower or "1 km" in lower):
        tags.append("world_knowledge_red_flag")

    if "made of candy" in lower:
        tags.append("absurdity_red_flag")

    return {
        "hard_block": bool(tags),
        "tags": tags,
    }


def verdict_from_score(score: int) -> str:
    if score < 55:
        return "High risk / do not rely"
    if score < 80:
        return "Unclear / needs verification"
    return "Likely reliable (still verify for high-stakes use)"


def tokenize_keywords(s: str) -> List[str]:
    # keep simple, deterministic
    s = re.sub(r"[^a-zA-Z0-9\s%]", " ", (s or "").lower())
    parts = [p for p in s.split() if p and p not in STOPWORDS]
    # drop pure numbers except clinically meaningful short tokens? keep %, years? keep words only:
    out = []
    for p in parts:
        if p.isdigit():
            continue
        out.append(p)
    return out


def evidence_relevance_mvp(claim_text: str, evidence_text: str, pmids: List[str]) -> Dict[str, Any]:
    """
    MVP relevance validation (NOT truth):
      Valid if:
        - Any PMID appears in claim text, OR
        - Keyword overlap between claim and evidence >= threshold
    """
    claim_lower = (claim_text or "").lower()

    # 1) PMID appears in claim text (explicit match)
    for pmid in pmids or []:
        if pmid and pmid in claim_lower:
            return {"validated": True, "reason": "pmid_in_claim", "overlap_terms": [pmid]}

    # 2) keyword overlap
    claim_terms = set(tokenize_keywords(claim_text))
    evid_terms = set(tokenize_keywords(evidence_text))

    overlap = sorted(list(claim_terms.intersection(evid_terms)))

    # threshold: require >=2 overlap terms for "validated" (tuneable)
    if len(overlap) >= 2:
        return {"validated": True, "reason": "keyword_overlap", "overlap_terms": overlap[:10]}

    if not evidence_text or not evidence_text.strip():
        return {"validated": False, "reason": "no_evidence", "overlap_terms": []}

    return {"validated": False, "reason": "no_overlap", "overlap_terms": overlap[:10]}


# ---------------------------------------
# Core scoring
# ---------------------------------------
def score_claim(text: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
    sig = compute_signals(text)
    claim_type = infer_claim_type(text, sig)

    score = 80
    score -= min(sig["absolute_count"] * 3, 15)

    risk_tags: List[str] = []

    high_liability = claim_type in {
        "numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"
    }

    evidence_present = bool(evidence.get("provided")) and (
        evidence.get("has_pmid") or evidence.get("has_doi") or evidence.get("has_url")
    )

    # Relevance validation (MVP)
    relevance = {"validated": False, "reason": "no_evidence", "overlap_terms": []}
    if evidence_present:
        relevance = evidence_relevance_mvp(
            claim_text=text,
            evidence_text=str(evidence.get("raw", "")),
            pmids=list(evidence.get("pmids", [])),
        )

    evidence_validated = evidence_present and bool(relevance.get("validated"))

    # High liability requires VALIDATED evidence (not merely "present")
    if high_liability and not evidence_validated:
        risk_tags.append("evidence_unvalidated")
        # cap score into REVIEW zone
        score = min(score, 75)

    # World knowledge hard block
    impl = implausibility_check(text)
    if impl["hard_block"]:
        score = min(score, 20)
        risk_tags.extend(impl["tags"])

    score = max(0, min(100, int(score)))

    # evidence-needed structure (only when unvalidated on high-liability)
    evidence_needed = None
    if "evidence_unvalidated" in risk_tags and high_liability:
        evidence_needed = {
            "required": True,
            "reason": "High-liability claim requires validated evidence relevance (MVP). Provide DOI/PMID/URL that matches the claim.",
            "acceptable_evidence_examples": [
                "PubMed URL for the specific claim",
                "DOI for the cited trial or meta-analysis",
                "Guideline URL directly supporting the claim"
            ],
            "suggested_query": f"{text} clinical trial meta-analysis PMID"
        }

    return {
        "text": text,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": sorted(list(set(risk_tags))),
        "score": score,
        "verdict": verdict_from_score(score),
        "evidence_needed": evidence_needed,
        "evidence_relevance_mvp": relevance if evidence_present else {
            "validated": False,
            "reason": "no_evidence",
            "overlap_terms": []
        }
    }


# ---------------------------------------
# Decision Gate (STEP 5)
# ---------------------------------------
def decision_gate(overall_score: int, claims: List[Dict[str, Any]], policy_mode: str) -> Dict[str, Any]:
    pm = (policy_mode or "enterprise").lower()

    # 1) World knowledge hard block overrides everything
    for c in claims:
        if "world_knowledge_red_flag" in c.get("risk_tags", []) or "absurdity_red_flag" in c.get("risk_tags", []):
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "World-knowledge red flag (hard block)."
            }

    # 2) High-liability without validated evidence
    if any("evidence_unvalidated" in c.get("risk_tags", []) for c in claims):
        if pm == "regulated":
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "High-liability claim without validated evidence."
            }
        return {
            "action": "REVIEW",
            "policy_mode": pm,
            "reason": "High-liability claim requires validated evidence; human review required."
        }

    # 3) Score-based rules
    if overall_score >= 80:
        return {"action": "ALLOW", "policy_mode": pm, "reason": "Meets trust threshold."}

    if overall_score < 55:
        return {"action": "BLOCK", "policy_mode": pm, "reason": "Low trust score."}

    return {"action": "REVIEW", "policy_mode": pm, "reason": "Intermediate confidence."}


# ---------------------------------------
# API
# ---------------------------------------
@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing text")

    policy_mode = req.policy_mode or "enterprise"

    # Evidence from evidence box
    evidence_info = detect_evidence(req.evidence)

    # Promote PMID/DOI/URL from claim text ONLY if evidence box empty
    if not evidence_info.get("provided"):
        promoted = detect_evidence(text)
        if promoted.get("has_pmid") or promoted.get("has_doi") or promoted.get("has_url"):
            evidence_info = promoted

    # Claims (MVP: single-claim; optional parser support)
    claims_raw: List[str] = []
    if extract_claims:
        try:
            parsed = extract_claims(text)
            if isinstance(parsed, list) and parsed:
                for c in parsed:
                    if isinstance(c, dict) and "text" in c:
                        claims_raw.append(str(c["text"]).strip())
                    elif isinstance(c, str):
                        claims_raw.append(c.strip())
        except Exception:
            claims_raw = []

    if not claims_raw:
        claims_raw = [text]

    scored_claims = [score_claim(c, evidence_info) for c in claims_raw if c]
    overall_score = min([c["score"] for c in scored_claims]) if scored_claims else 55

    decision = decision_gate(overall_score, scored_claims, policy_mode)

    fingerprint = hashlib.sha256(
        f"{text}|{req.evidence or ''}|{overall_score}|{policy_mode}".encode("utf-8")
    ).hexdigest()

    return {
        "audit_fingerprint": {
            "sha256": fingerprint,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        },
        "event_id": fingerprint[:12],
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
            "policy_mode": policy_mode,
        },
        "score": overall_score,
        "verdict": verdict_from_score(overall_score),
        "decision": decision,
        "claims": scored_claims,
        "explanation": (
            "MVP heuristic verification + Decision Gate. "
            "High-liability claims require evidence presence AND MVP relevance validation "
            "(PMID-in-claim or keyword overlap)."
        ),
        "evidence": {
            "provided": bool(evidence_info.get("provided")),
            "signals": {
                "has_url": bool(evidence_info.get("has_url")),
                "has_doi": bool(evidence_info.get("has_doi")),
                "has_pmid": bool(evidence_info.get("has_pmid")),
                "has_pubmed_url": bool(evidence_info.get("has_pubmed_url")),
                "pmids": list(evidence_info.get("pmids", [])),
            },
            "raw": evidence_info.get("raw", ""),
        }
    }
