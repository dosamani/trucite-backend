import os
import hashlib
import uuid
from datetime import datetime
from urllib.parse import urlparse, unquote

from flask import Flask, request, jsonify
import psycopg2

app = Flask(__name__)

# ---------------------------------------------------------
# DB Helpers (No URI passed to psycopg2.connect)
# ---------------------------------------------------------

def mask_db_url(db_url: str) -> str:
    if not db_url:
        return ""
    try:
        if "://" in db_url and "@" in db_url:
            proto, rest = db_url.split("://", 1)
            creds, host = rest.split("@", 1)
            user = creds.split(":", 1)[0]
            return f"{proto}://{user}:***@{host}"
    except Exception:
        pass
    return db_url

def parse_db_url(db_url: str):
    """
    Parse postgresql://user:pass@host:port/dbname?...
    Returns dict suitable for psycopg2.connect(**kwargs)
    """
    p = urlparse(db_url)
    if p.scheme not in ("postgres", "postgresql"):
        raise ValueError(f"Unsupported DB scheme: {p.scheme}")

    user = unquote(p.username) if p.username else None
    password = unquote(p.password) if p.password else None
    host = p.hostname
    port = p.port or 5432
    dbname = (p.path or "").lstrip("/") or "postgres"

    if not host or not user or password is None:
        raise ValueError("DB URL missing host/user/password")

    return {
        "host": host,
        "port": port,
        "dbname": dbname,
        "user": user,
        "password": password,
        "sslmode": "require",
        "connect_timeout": 10,
    }

def get_db_conn():
    """
    Priority:
    1) If DATABASE_URL is set, parse it and connect with keyword args.
    2) Else use separate env vars: DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    """
    db_url = (os.getenv("DATABASE_URL") or "").strip()

    if db_url:
        # IMPORTANT: use your FULL URL with password already included.
        # Psycopg2 will NOT accept DSN-style parsing errors if we avoid passing the URI.
        kwargs = parse_db_url(db_url)
        return psycopg2.connect(**kwargs)

    # Fallback: separate vars
    host = os.getenv("DB_HOST", "").strip()
    port = int(os.getenv("DB_PORT", "5432"))
    dbname = os.getenv("DB_NAME", "postgres").strip()
    user = os.getenv("DB_USER", "postgres").strip()
    password = os.getenv("DB_PASSWORD", "").strip()

    if not host or not password:
        raise RuntimeError("DATABASE_URL not set and DB_HOST/DB_PASSWORD not set")

    return psycopg2.connect(
        host=host,
        port=port,
        dbname=dbname,
        user=user,
        password=password,
        sslmode="require",
        connect_timeout=10,
    )

# ---------------------------------------------------------
# Database Startup Test
# ---------------------------------------------------------

def db_startup_ping():
    db_url = (os.getenv("DATABASE_URL") or "").strip()
    if db_url:
        print(f"[DB] DATABASE_URL detected: {mask_db_url(db_url)}")
    else:
        print("[DB] DATABASE_URL not set; using DB_HOST/DB_USER/DB_PASSWORD vars")

    conn = None
    cur = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("select now();")
        ts = cur.fetchone()[0]
        print(f"[DB] Startup ping OK. Server time: {ts}")
    except Exception as e:
        print(f"[DB] Startup ping FAILED: {repr(e)}")
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

db_startup_ping()

# ---------------------------------------------------------
# Utility
# ---------------------------------------------------------

def normalize_claim(text):
    return " ".join((text or "").lower().strip().split())

def fingerprint_claim(text):
    return hashlib.sha256((text or "").encode()).hexdigest()

# ---------------------------------------------------------
# API Endpoint
# ---------------------------------------------------------

@app.route("/verify", methods=["POST"])
def verify():
    data = request.get_json(silent=True) or {}
    text = data.get("text", "")

    normalized = normalize_claim(text)
    claim_fp = fingerprint_claim(normalized)
    event_id = str(uuid.uuid4())

    score = 54
    verdict = "Questionable / High Uncertainty"

    response = {
        "audit_fingerprint": {
            "engine_version": "TruCite Claim Engine v2.4 (MVP)",
            "hash": claim_fp,
            "timestamp_utc": datetime.utcnow().isoformat() + "Z"
        },
        "claims": [{
            "confidence_weight": 1,
            "id": "c1",
            "text": text,
            "type": "factual"
        }],
        "event_id": event_id,
        "score": score,
        "verdict": verdict
    }

    conn = None
    cur = None
    try:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute(
            """
            insert into public.trucite_events
            (event_id, claim_fingerprint, normalized_claim, score, engine_version)
            values (%s, %s, %s, %s, %s)
            """,
            (event_id, claim_fp, normalized, score, "v2.4")
        )
        conn.commit()
        print("[DB] Insert OK")
    except Exception as e:
        print(f"[DB] Insert failed: {repr(e)}")
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass

    return jsonify(response)

# ---------------------------------------------------------
# Run
# ---------------------------------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
