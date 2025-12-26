import re

FACTUAL_KEYWORDS = [" is ", " are ", " was ", " were ", " has ", " have ", " will ", " contains "]

def classify_claim(text: str) -> str:
    t = f" {text.lower().strip()} "
    if any(k in t for k in FACTUAL_KEYWORDS):
        return "factual"
    return "unknown"

def parse_claims(raw_text: str):
    raw_text = (raw_text or "").strip()
    if not raw_text:
        return []

    # split into sentences (simple + stable)
    sentences = re.split(r'(?<=[.!?])\s+', raw_text)

    claims = []
    idx = 1
    for s in sentences:
        s = (s or "").strip()
        if not s:
            continue
        claims.append({
            "id": f"c{idx}",
            "text": s,
            "type": classify_claim(s),
            "confidence_weight": 1.0
        })
        idx += 1

    return claims
