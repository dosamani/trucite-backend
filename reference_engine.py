# reference_engine.py

def find_references(claims):
    references = []

    for claim in claims:
        if "moon" in claim.lower():
            references.append({
                "claim": claim,
                "source": "NASA Lunar Science",
                "url": "https://science.nasa.gov/moon/"
            })

    return references
