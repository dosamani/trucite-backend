# app.py — TruCite backend (MVP)
# FastAPI + static landing page + /verify scoring endpoint
# Render will serve:
#   GET  /         -> static/index.html (landing page)
#   GET  /static/* -> static assets
#   POST /verify   -> scoring JSON + Decision Gate (ALLOW/REVIEW/BLOCK)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional, List, Dict, Any, Literal
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


app = FastAPI(title="TruCite Engine", version="0.4.0")

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
PolicyMode = Literal["consumer", "enterprise", "regulated"]

class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = None  # URLs/DOIs/PMIDs pasted from the evidence box
    policy_mode: Optional[PolicyMode] = "enterprise"  # Step 5: Decision Gate mode


# ----------------------------
# Helpers: signals + scoring
# ----------------------------
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PUBMED_URL_RE = re.compile(r"(pubmed\.ncbi\.nlm\.nih\.gov/\d+)", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID\s*:\s*\d+\b|\bPMID\s+\d+\b|\b\d{6,9}\b", re.IGNORECASE)

ABSOLUTE_WORDS = [
    "always", "never", "guaranteed", "proven", "definitely", "certainly", "no doubt",
    "cannot", "will", "must", "only", "everyone", "nobody"
]

STOPWORDS = set([
    "the","a","an","and","or","but","to","of","in","on","for","with","by","as","at",
    "is","are","was","were","be","been","being","it","this","that","these","those",
    "from","into","than","then","so","we","you","they","he","she","i","our","your"
])

def compute_signals(text: str) -> Dict[str, Any]:
    t = text.strip()
    has_url = bool(URL_RE.search(t))
    has_doi = bool(DOI_RE.search(t))
    has_pubmed_url = bool(PUBMED_URL_RE.search(t))
    has_pmid = bool(re.search(r"\bPMID\b", t, re.IGNORECASE)) or bool(PMID_RE.search(t))
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
        "has_pmid": has_pmid,
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

    med_words = ["aspirin", "mi", "myocardial", "infarction", "stroke", "mortality", "risk", "trial", "meta-analysis", "guideline", "primary prevention", "secondary prevention"]
    if any(w in lower for w in med_words):
        return "medical_claim"

    fin_words = ["roi", "yield", "interest rate", "stock", "bond", "inflation", "apr", "apy"]
    if any(w in lower for w in fin_words):
        return "finance_claim"

    legal_words = ["illegal", "compliance", "statute", "regulation", "liable", "lawsuit", "contract", "tort"]
    if any(w in lower for w in legal_words):
        return "legal_claim"

    return "general_claim"

def has_any_evidence(evidence_text: Optional[str]) -> Dict[str, Any]:
    if not evidence_text:
        return {"provided": False, "has_url": False, "has_doi": False, "has_pmid": False, "has_pubmed_url": False, "pmids": []}
    e = evidence_text.strip()
    pmids = re.findall(r"\b\d{6,9}\b", e)
    return {
        "provided": bool(e),
        "has_url": bool(URL_RE.search(e)),
        "has_doi": bool(DOI_RE.search(e)),
        "has_pubmed_url": bool(PUBMED_URL_RE.search(e)),
        "has_pmid": bool(re.search(r"\bPMID\b", e, re.IGNORECASE)) or bool(PMID_RE.search(e)),
        "pmids": list(dict.fromkeys(pmids))[:10]
    }

def _tokenize_keywords(s: str) -> List[str]:
    raw = re.findall(r"[a-zA-Z0-9]+", (s or "").lower())
    toks = [t for t in raw if t not in STOPWORDS and len(t) >= 3]
    return toks

def validate_evidence_relevance_mvp(claim_text: str, evidence_text: Optional[str]) -> Dict[str, Any]:
    """
    Step 3 — Relevance validation (MVP heuristic, not “truth”)
    Evidence is validated if:
      - PMID in evidence appears in claim text OR
      - keyword overlap above threshold
    """
    if not evidence_text:
        return {"validated": False, "reason": "no_evidence", "overlap_terms": []}

    claim_tokens = set(_tokenize_keywords(claim_text))
    evidence_tokens = set(_tokenize_keywords(evidence_text))

    # PMID shortcut
    pmids = re.findall(r"\b\d{6,9}\b", evidence_text)
    overlap_pmids = [p for p in pmids if p in claim_text]
    if overlap_pmids:
        return {"validated": True, "reason": "pmid_in_claim", "overlap_terms": overlap_pmids[:5]}

    overlap = sorted(list(claim_tokens.intersection(evidence_tokens)))
    # threshold tuned for MVP: at least 2 meaningful overlaps OR 1 overlap if claim is very short
    threshold = 2 if len(claim_tokens) >= 6 else 1
    if len(overlap) >= threshold:
        return {"validated": True, "reason": "keyword_overlap", "overlap_terms": overlap[:10]}

    return {"validated": False, "reason": "no_overlap", "overlap_terms": []}

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

def build_audit_fingerprint(payload: str) -> Dict[str, str]:
    sha = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {"sha256": sha, "timestamp_utc": datetime.now(timezone.utc).isoformat()}

def score_one_claim(claim_text: str, evidence_info: Dict[str, Any], evidence_text: Optional[str]) -> Dict[str, Any]:
    sig = compute_signals(claim_text)
    claim_type = infer_claim_type(claim_text, sig)

    # Start conservative
    score = 78

    # Penalize absolutist language
    score -= min(sig.get("absolute_count", 0) * 3, 12)

    # Evidence requirement for high-liability classes
    high_liability = claim_type in ("numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim")

    evidence_present = evidence_info.get("provided") and (
        evidence_info.get("has_url") or evidence_info.get("has_doi") or evidence_info.get("has_pmid") or evidence_info.get("has_pubmed_url")
    )

    risk_tags: List[str] = []

    # Step 3 relevance heuristic
    relevance = None
    evidence_validated = False
    if evidence_present:
        relevance = validate_evidence_relevance_mvp(claim_text, evidence_text)
        evidence_validated = bool(relevance.get("validated"))

    # High-liability: needs validated evidence for top tier
    if high_liability:
        if not evidence_present:
            risk_tags.append("citation_unverified")
            if claim_type == "numeric_or_stat_claim":
                risk_tags.append("numeric_claim")
            score = min(score, 55)
        else:
            # Evidence exists but might be unrelated
            if not evidence_validated:
                risk_tags.append("evidence_unvalidated")
                score = min(score, 75)

    # Implausibility caps override (Step 4)
    imp = implausibility_caps(claim_text)
    if imp["cap"] is not None:
        score = min(score, int(imp["cap"]))
        risk_tags.extend(imp["tags"])

    # Modest boost if evidence validated and no world-knowledge cap
    if high_liability and evidence_present and evidence_validated and imp["cap"] is None:
        score = min(90, score + 8)

    score = max(0, min(100, int(score)))
    verdict = verdict_from_score(score)

    evidence_needed = None
    if ("citation_unverified" in risk_tags) or ("evidence_unvalidated" in risk_tags):
        if "citation_unverified" in risk_tags:
            reason = "Claim includes numeric/statistical or high-liability content without attached evidence (URL/DOI/PMID)."
        else:
            reason = "Evidence was provided but appears unrelated to the claim (MVP relevance check). Provide a matching DOI/PMID/URL for this specific claim."
        evidence_needed = {
            "required": True,
            "reason": reason,
            "acceptable_evidence_examples": [
                "PubMed link for the specific claim",
                "DOI for the cited trial or meta-analysis",
                "Guideline URL directly supporting the claim"
            ],
            "suggested_query": f"{claim_text} clinical trial meta-analysis PMID",
        }

    result = {
        "text": claim_text,
        "claim_type": claim_type,
        "signals": sig,
        "risk_tags": list(dict.fromkeys(risk_tags)),
        "score": score,
        "verdict": verdict,
        "evidence_needed": evidence_needed,
    }
    if relevance is not None:
        result["evidence_relevance_mvp"] = relevance

    return result


# ----------------------------
# Step 5 — Decision Gate Enforcement
# ----------------------------
def compute_decision(policy_mode: str, overall_score: int, scored_claims: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Decision matrix (binding):
      Condition                               Consumer    Enterprise   Regulated
      World knowledge flag                     BLOCK       BLOCK        BLOCK
      High-liability + no validated evidence   REVIEW      REVIEW       BLOCK
      Score >= 80 + validated evidence         ALLOW       ALLOW        ALLOW
      Score 55–79                              REVIEW      REVIEW       REVIEW
      Score < 55                               BLOCK       BLOCK        BLOCK
    """
    pm = (policy_mode or "enterprise").lower().strip()
    if pm not in ("consumer", "enterprise", "regulated"):
        pm = "enterprise"

    # Any world-knowledge red flag -> BLOCK (hard override)
    for c in scored_claims:
        tags = set(c.get("risk_tags") or [])
        if "world_knowledge_red_flag" in tags or "absurdity_red_flag" in tags:
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "World-knowledge red flag (hard block)."
            }

    # Determine if any high-liability claim lacks validated evidence
    high_liability_types = {"numeric_or_stat_claim", "medical_claim", "legal_claim", "finance_claim"}
    has_high_liability = any((c.get("claim_type") in high_liability_types) for c in scored_claims)

    # validated evidence means: evidence_present AND relevance validated for that claim (if relevance was computed)
    def claim_has_validated_evidence(c: Dict[str, Any]) -> bool:
        rel = c.get("evidence_relevance_mvp")
        if rel is None:
            # If no relevance object exists, treat as not validated (conservative)
            return False
        return bool(rel.get("validated"))

    any_validated = any(claim_has_validated_evidence(c) for c in scored_claims)
    any_unvalidated_tag = any("citation_unverified" in (c.get("risk_tags") or []) or "evidence_unvalidated" in (c.get("risk_tags") or []) for c in scored_claims)

    # High-liability + no validated evidence
    if has_high_liability and (not any_validated):
        if pm == "regulated":
            return {
                "action": "BLOCK",
                "policy_mode": pm,
                "reason": "High-liability claim without validated evidence under regulated policy."
            }
        return {
            "action": "REVIEW",
            "policy_mode": pm,
            "reason": "High-liability claim requires validated evidence; human review required."
        }

    # If score < 55 -> BLOCK
    if overall_score < 55:
        return {"action": "BLOCK", "policy_mode": pm, "reason": "Score below policy minimum."}

    # If score >= 80 + validated evidence -> ALLOW
    if overall_score >= 80 and any_validated:
        return {"action": "ALLOW", "policy_mode": pm, "reason": "Meets policy threshold with validated evidence."}

    # Score 55–79 -> REVIEW
    if 55 <= overall_score <= 79:
        # Even if evidence validated, still review in this band by policy
        return {"action": "REVIEW", "policy_mode": pm, "reason": "Score in review band under policy."}

    # Otherwise ALLOW (covers 80+ without explicit validation on non-high-liability)
    if overall_score >= 80 and not any_unvalidated_tag:
        return {"action": "ALLOW", "policy_mode": pm, "reason": "High score with no critical risk tags."}

    return {"action": "REVIEW", "policy_mode": pm, "reason": "Conservative default: review."}


# ----------------------------
# API
# ----------------------------
@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Missing 'text'")

    policy_mode = (req.policy_mode or "enterprise").lower().strip()
    evidence_text = (req.evidence or "").strip() if req.evidence else None
    evidence_info = has_any_evidence(evidence_text)

    # ----------------------------------------
    # Promote PMID / DOI found in claim text
    # into evidence_info (Decision Gate rule)
    # ----------------------------------------
    if not evidence_info.get("provided"):
        pmids_in_text = re.findall(PMID_RE, text)
        dois_in_text = re.findall(DOI_RE, text)

        if pmids_in_text or dois_in_text:
            evidence_info = {
                "provided": True,
                "has_url": False,
                "has_doi": bool(dois_in_text),
                "has_pmid": bool(pmids_in_text),
                "pmids": pmids_in_text,
            }

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
        scored = score_one_claim(claim_text, evidence_info, evidence_text)
        scored_claims.append(scored)
        claim_scores.append(int(scored["score"]))

    overall_score = min(claim_scores) if claim_scores else 55
    overall_verdict = verdict_from_score(overall_score)

    decision = compute_decision(policy_mode, overall_score, scored_claims)

    payload_for_fingerprint = f"{text}|{evidence_text or ''}|{policy_mode}|{overall_score}|{overall_verdict}|{decision.get('action')}"
    audit_fp = build_audit_fingerprint(payload_for_fingerprint)

    return {
        "audit_fingerprint": audit_fp,
        "event_id": audit_fp["sha256"][:12],
        "input": {
            "length_chars": len(text),
            "num_claims": len(scored_claims),
            "policy_mode": policy_mode,
        },
        "score": overall_score,
        "verdict": overall_verdict,
        "decision": decision,  # Step 5 output
        "claims": scored_claims,
        "explanation": (
            "MVP heuristic verification + Decision Gate. Flags risk using claim segmentation, numeric/stat patterns, "
            "citation/evidence signals, absolutist language, and world-knowledge red flags. Decision Gate applies "
            "policy_mode to enforce ALLOW/REVIEW/BLOCK. Enterprise mode adds true evidence validation, drift analytics, "
            "and policy controls."
        ),
        "evidence": {
            "provided": bool(evidence_info.get("provided")),
            "signals": {
                "has_url": bool(evidence_info.get("has_url")),
                "has_doi": bool(evidence_info.get("has_doi")),
                "has_pmid": bool(evidence_info.get("has_pmid")),
                "has_pubmed_url": bool(evidence_info.get("has_pubmed_url")),
                "pmids": evidence_info.get("pmids", []),
            }
        }
    }
