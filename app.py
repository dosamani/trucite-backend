# app.py — TruCite backend (MVP)
# Adds:
# 1) Implausible/world-knowledge red-flag rules (implausible_claim, distance_implausible, material_implausible, etc.)
# 2) Evidence-aware rescoring (PMID/DOI/URL reduces citation_unverified penalty; weak evidence is partial)

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import hashlib
import re
from datetime import datetime, timezone

app = FastAPI(title="TruCite Engine", version="0.3.0")

# CORS: keep permissive for MVP demo
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ----------------------------
# Models
# ----------------------------
class VerifyRequest(BaseModel):
    text: str
    evidence: Optional[str] = ""  # optional sources pasted by user (URLs/DOIs/PMIDs/citations)

# ----------------------------
# Simple in-memory drift (MVP)
# ----------------------------
DRIFT_MEMORY: Dict[str, Dict[str, Any]] = {}  # key=fingerprint -> {score, verdict, timestamp_utc, claim_count}

# ----------------------------
# Utility: hashing / fingerprints
# ----------------------------
def sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()

# ----------------------------
# Claim segmentation (MVP)
# ----------------------------
def segment_claims(text: str) -> List[str]:
    # Conservative segmentation: split on newlines, semicolons, sentence endings
    raw = re.split(r"[\n;]+|(?<=[\.\?\!])\s+", text.strip())
    claims = [c.strip() for c in raw if c and c.strip()]
    # Hard cap for MVP safety
    return claims[:8] if claims else []

# ----------------------------
# Evidence parsing
# ----------------------------
URL_RE = re.compile(r"(https?://[^\s\)]+)", re.IGNORECASE)
DOI_RE = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)
PMID_RE = re.compile(r"\bPMID\s*:\s*\d+\b|\bPMID\s*\d+\b|\b\d{7,9}\b", re.IGNORECASE)  # includes bare 7-9 digits as weak PMID hint

def parse_evidence(evidence: str) -> Dict[str, Any]:
    ev = (evidence or "").strip()
    urls = URL_RE.findall(ev) if ev else []
    dois = DOI_RE.findall(ev) if ev else []
    pmids = PMID_RE.findall(ev) if ev else []

    # "Strong" evidence: URL or DOI or explicit PMID label
    has_strong = bool(urls or dois or re.search(r"\bPMID\b", ev, re.IGNORECASE))
    # "Weak" evidence: some citation-ish text but no identifiers
    has_any_text = bool(ev)
    has_weak = has_any_text and not has_strong

    return {
        "has_sources_provided": has_any_text,
        "has_url": bool(urls),
        "has_doi": bool(dois),
        "has_pmid": bool(pmids),
        "urls": urls[:5],
        "dois": list({d.lower() for d in dois})[:5],
        "pmids": pmids[:5],
        "strength": "strong" if has_strong else ("weak" if has_weak else "none"),
    }

# ----------------------------
# Signal extraction
# ----------------------------
ABSOLUTE_WORDS = ["always", "never", "guaranteed", "proves", "proven", "cure", "100%", "no risk"]
HEDGE_WORDS = ["may", "might", "could", "suggests", "likely", "possibly", "uncertain", "preliminary"]

def extract_signals(claim: str, evidence_meta: Dict[str, Any]) -> Dict[str, Any]:
    c = claim.lower()

    numerics = re.findall(r"\b\d+(\.\d+)?\b", claim)
    has_percent = "%" in claim
    has_year = bool(re.search(r"\b(19\d{2}|20\d{2})\b", claim))
    has_citation_like = bool(has_year) or bool(re.search(r"\b(et al\.|randomized|trial|meta-analysis|systematic review)\b", c))

    absolute_count = sum(1 for w in ABSOLUTE_WORDS if w in c)
    hedge_count = sum(1 for w in HEDGE_WORDS if w in c)

    return {
        "numeric_count": len(numerics),
        "has_numerics": len(numerics) > 0,
        "has_percent": has_percent,
        "has_year": has_year,
        "has_citation_like": has_citation_like,
        "has_url": evidence_meta.get("has_url", False),
        "has_doi": evidence_meta.get("has_doi", False),
        "has_sources_provided": evidence_meta.get("has_sources_provided", False),
        "absolute_count": absolute_count,
        "hedge_count": hedge_count,
    }

# ----------------------------
# Feature 1: World-knowledge / implausibility rules (MVP)
# ----------------------------
DISTANCE_PAT = re.compile(r"\b(\d+(?:\.\d+)?)\s*(km|kilometer|kilometers|m|meter|meters)\b", re.IGNORECASE)

def world_knowledge_risk_tags(claim: str) -> List[str]:
    c = claim.lower()
    tags = []

    # blatant absurd material/biology cues
    if any(x in c for x in ["made up of candy", "made of candy", "made of chocolate", "made of marshmallow"]):
        tags.append("material_implausible")
        tags.append("implausible_claim")

    # moon distance sanity check
    # If claim mentions moon and gives a tiny distance in km/m, flag.
    if "moon" in c:
        m = DISTANCE_PAT.search(claim)
        if m:
            val = float(m.group(1))
            unit = m.group(2).lower()
            # convert to km
            km = val / 1000.0 if unit.startswith("m") else val
            # anything < 10,000 km is absurd for Earth-Moon distance
            if km < 10000:
                tags.append("distance_implausible")
                tags.append("implausible_claim")

    # other quick absurdities (keep minimal to avoid false positives)
    if any(x in c for x in ["sun is cold", "humans breathe water", "earth is flat"]):
        tags.append("implausible_claim")

    return list(dict.fromkeys(tags))  # de-dup preserving order

# ----------------------------
# Scoring logic (MVP heuristics)
# ----------------------------
def evidence_needed_block(claim: str, claim_type: str) -> Dict[str, Any]:
    # Light guidance for demo UX
    return {
        "required": True,
        "reason": "Claim includes numeric/statistical or citation-like content without an attached source (URL/DOI) or provided evidence.",
        "acceptable_evidence_examples": [
            "Peer-reviewed paper link (DOI/PMID/URL)",
            "Clinical guideline link (e.g., society guideline URL)",
            "Regulatory label / official statement URL",
            "Internal policy document reference (enterprise mode)"
        ],
        "suggested_query": f"{claim} clinical trial meta-analysis PMID"
    }

def base_score_from_signals(signals: Dict[str, Any]) -> int:
    # Start at 80 (neutral-ish), subtract risk, add small credit for hedging
    score = 80

    # numeric/stat claims without evidence are risky
    if signals["has_numerics"]:
        score -= 12
    if signals["has_percent"]:
        score -= 10
    if signals["has_citation_like"]:
        score -= 10
    if signals["absolute_count"] > 0:
        score -= 10 * min(signals["absolute_count"], 2)

    # hedging indicates awareness of uncertainty (slight positive)
    if signals["hedge_count"] > 0:
        score += min(signals["hedge_count"], 2) * 2

    return max(0, min(100, score))

def apply_world_knowledge_penalty(score: int, wk_tags: List[str]) -> int:
    # If implausible, force score down meaningfully
    if "implausible_claim" in wk_tags:
        score -= 25
    if "distance_implausible" in wk_tags:
        score -= 15
    if "material_implausible" in wk_tags:
        score -= 10
    return max(0, min(100, score))

def apply_evidence_adjustment(score: int, signals: Dict[str, Any], evidence_meta: Dict[str, Any]) -> int:
    """
    Feature 2: evidence-aware rescoring.
    - If the claim looks citation-like or numeric and user provided STRONG evidence (URL/DOI/explicit PMID), restore points.
    - If weak evidence (text but no identifiers), restore fewer points.
    """
    needs_evidence = (signals["has_numerics"] or signals["has_percent"] or signals["has_citation_like"] or signals["has_year"])

    if not needs_evidence:
        return score

    strength = evidence_meta.get("strength", "none")

    if strength == "strong":
        # restore risk penalties substantially
        score += 18
    elif strength == "weak":
        # restore a little, still likely "needs verification"
        score += 8
    else:
        # none: no change
        pass

    return max(0, min(100, score))

def verdict_from_score(score: int) -> str:
    if score >= 85:
        return "Approve / low risk"
    if score >= 65:
        return "Unclear / needs verification"
    return "High risk / do not rely"

def claim_type_from_signals(signals: Dict[str, Any], wk_tags: List[str]) -> str:
    if "implausible_claim" in wk_tags:
        return "world_knowledge_conflict"
    if signals["has_numerics"] or signals["has_percent"]:
        return "numeric_or_stat_claim"
    return "general_claim"

# ----------------------------
# Routes
# ----------------------------
@app.get("/health")
def health():
    return {"status": "ok", "service": "trucite-engine", "version": app.version}

@app.post("/verify")
def verify(req: VerifyRequest):
    text = (req.text or "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Empty text")

    evidence_meta = parse_evidence(req.evidence or "")

    # Segment claims
    claims = segment_claims(text)
    if not claims:
        claims = [text[:800]]

    claim_results = []
    overall_scores = []

    for claim in claims:
        wk_tags = world_knowledge_risk_tags(claim)
        signals = extract_signals(claim, evidence_meta)

        # baseline
        score = base_score_from_signals(signals)
        # world-knowledge penalty
        score = apply_world_knowledge_penalty(score, wk_tags)
        # evidence adjustment
        score = apply_evidence_adjustment(score, signals, evidence_meta)

        ctype = claim_type_from_signals(signals, wk_tags)
        verdict = verdict_from_score(score)

        # risk tags
        risk_tags = []

        # numeric/citation risk
        if signals["has_numerics"]:
            risk_tags.append("numeric_claim")
        if signals["has_citation_like"] or signals["has_year"]:
            # only flag as unverified if no strong evidence
            if evidence_meta.get("strength") != "strong":
                risk_tags.append("citation_unverified")

        # world-knowledge tags
        risk_tags.extend(wk_tags)

        # evidence needed block (only for numeric/citation type + no strong evidence)
        evidence_needed = None
        if (ctype in ["numeric_or_stat_claim"] or signals["has_citation_like"] or signals["has_year"]) and evidence_meta.get("strength") != "strong":
            evidence_needed = evidence_needed_block(claim, ctype)

        claim_results.append({
            "text": claim,
            "score": score,
            "verdict": verdict,
            "risk_tags": list(dict.fromkeys(risk_tags)),
            "signals": signals,
            "claim_type": ctype,
            "evidence_needed": evidence_needed
        })
        overall_scores.append(score)

    # Overall score: mean (simple MVP)
    overall = int(round(sum(overall_scores) / max(1, len(overall_scores))))
    overall_verdict = verdict_from_score(overall)

    # Audit fingerprint (input+evidence)
    fp_source = f"{text}\n---EVIDENCE---\n{(req.evidence or '').strip()}"
    fp = sha256_text(fp_source)
    event_id = fp[:12]

    # Drift: compare same fingerprint (MVP in-memory)
    drift = {
        "has_prior": False,
        "prior_timestamp_utc": None,
        "score_delta": None,
        "verdict_changed": False,
        "drift_flag": False,
        "claim_count_delta": None,
        "notes": "MVP in-memory drift. Enterprise mode persists histories and compares behavior over time."
    }

    prior = DRIFT_MEMORY.get(fp)
    if prior:
        drift["has_prior"] = True
        drift["prior_timestamp_utc"] = prior["timestamp_utc"]
        drift["score_delta"] = overall - prior["score"]
        drift["claim_count_delta"] = len(claim_results) - prior.get("claim_count", len(claim_results))
        drift["verdict_changed"] = (overall_verdict != prior["verdict"])
        # drift flag if score moves a lot OR verdict changes
        drift["drift_flag"] = drift["verdict_changed"] or (abs(drift["score_delta"]) >= 15)

    # Persist this run
    DRIFT_MEMORY[fp] = {
        "score": overall,
        "verdict": overall_verdict,
        "timestamp_utc": now_utc(),
        "claim_count": len(claim_results)
    }

    response = {
        "score": overall,
        "verdict": overall_verdict,
        "explanation": (
            "MVP heuristic verification. This demo flags risk via claim segmentation, numeric/stat patterns, "
            "citation signals, world-knowledge implausibility cues, absolute language, and uncertainty cues. "
            "Evidence (URLs/DOIs/PMIDs) reduces 'citation_unverified' risk. Enterprise mode adds evidence-backed "
            "checks, source validation, and persistent drift analytics."
        ),
        "input": {
            "length_chars": len(text),
            "num_claims": len(claim_results)
        },
        "claims": claim_results,
        "uncertainty_map": {
            "risk_tags": list(dict.fromkeys([t for c in claim_results for t in (c.get("risk_tags") or [])]))
        },
        "audit_fingerprint": {
            "sha256": fp,
            "timestamp_utc": now_utc()
        },
        "event_id": event_id,
        "drift": drift,
        "evidence_meta": evidence_meta
    }

    return response
