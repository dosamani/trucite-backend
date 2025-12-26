from flask import Flask, jsonify, Response
from datetime import datetime, timezone
import os

app = Flask(__name__)

FINGERPRINT = "TRUCITE_BACKEND_HTML_FINGERPRINT_v20251226_1705"

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

@app.get("/")
def home():
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <title>TruCite Backend Fingerprint</title>
</head>
<body style="font-family:Arial;background:#0b0b0b;color:#ffd54a;padding:24px;">
  <h2>TruCite Backend HTML is Live</h2>
  <p><b>FINGERPRINT:</b> {FINGERPRINT}</p>
  <p><b>UTC:</b> {utc_now_iso()}</p>
  <p>If you still see JSON at /, Render is not serving this app.py.</p>
</body>
</html>"""
    return Response(html, mimetype="text/html")

@app.get("/__whoami")
def whoami():
    return jsonify({
        "fingerprint": FINGERPRINT,
        "utc": utc_now_iso(),
        "cwd": os.getcwd(),
        "files_in_cwd": sorted(os.listdir("."))[:50],
        "env_has_port": "PORT" in os.environ,
    })

@app.get("/health")
def health():
    return jsonify({
        "service": "TruCite Backend",
        "status": "ok",
        "fingerprint": FINGERPRINT,
        "utc": utc_now_iso()
    })
