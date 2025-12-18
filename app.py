import os
from flask import Flask, request, jsonify, make_response

app = Flask(__name__)

# ------------------------------------------------------------
# CORS (Permanent fix)
# ------------------------------------------------------------
# Allow all origins for public demo.
# If you want to lock down later, set ALLOWED_ORIGINS env var
# e.g. "https://YOUR.neocities.org,https://trucite.ai"
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*")

def corsify_response(resp):
    origin = request.headers.get("Origin", "")

    # If ALLOWED_ORIGINS is "*", allow all.
    if ALLOWED_ORIGINS.strip() == "*":
        resp.headers["Access-Control-Allow-Origin"] = "*"
    else:
        allowed_list = [o.strip() for o in ALLOWED_ORIGINS.split(",") if o.strip()]
        if origin in allowed_list:
            resp.headers["Access-Control-Allow-Origin"] = origin
        else:
            # If origin not recognized, do NOT allow it
            resp.headers["Access-Control-Allow-Origin"] = "null"

    resp.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    resp.headers["Access-Control-Max-Age"] = "86400"
    return resp

@app.after_request
def add_cors_headers(resp):
    return corsify_response(resp)

# ------------------------------------------------------------
# Health check (optional)
# ------------------------------------------------------------
@app.get("/health")
def health():
    return jsonify({"ok": True})

# ------------------------------------------------------------
# IMPORTANT: Preflight handler (THIS is what fixes Neocities fetch)
# ------------------------------------------------------------
@app.route("/truth-score", methods=["OPTIONS"])
def truth_score_preflight():
    resp = make_response("", 204)
    return corsify_response(resp)

# ------------------------------------------------------------
# Your scoring endpoint
# ------------------------------------------------------------
@app.route("/truth-score", methods=["POST"])
def truth_score():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400

    # Demo scoring logic (replace later)
    # Keep it stable for now.
    score = 82
    verdict = "Likely reliable"

    return jsonify({
        "mode": "demo",
        "score": score,
        "verdict": verdict
    }), 200

# ------------------------------------------------------------
# Render/Gunicorn entry
# ------------------------------------------------------------
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
