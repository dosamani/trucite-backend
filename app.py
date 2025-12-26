import os
import json
import time
import uuid
import hashlib
from datetime import datetime, timezone

from flask import Flask, request, jsonify, send_from_directory

from claim_parser import extract_claims
from reference_engine import score_with_seed_corpus


app = Flask(__name__, static_folder="static", static_url_path="/static")

# === Integrity Chain storage (MVP) ===
# NOTE: On Render free instances, local files may not persist forever across deploys/restarts.
# This is still valuable for MVP + patent narrative. Later we move this into a DB.
CHAIN_PATH = os.getenv("TRUCITE_CHAIN_PATH", "integrity_chain.jsonl")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def load_last_event_hash():
    """
    Reads the last line of the JSONL chain file and returns its event_hash.
    If file doesn't exist or is empty, returns "GENESIS".
    """
    if not os.path.exists(CHAIN_PATH):
        return "GENESIS"
    try:
        with open(CHAIN_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return "GENESIS"

            # Read from end to find last newline
            pos = f.tell() - 1
            while pos > 0:
                f.seek(pos)
                if f.read(1) == b"\n":
                    break
                pos -= 1
            f.seek(pos + 1)
            last_line = f.read().decode("utf-8").strip()
            if not last_line:
                return "GENESIS"
            obj = json.loads(last_line)
            return obj.get("event_hash", "GENESIS")
    except Exception:
        return "GENESIS"


def append_chain_event(event_obj: dict) -> dict:
    """
    Adds prev_hash + event_hash and appends to JSONL file.
    """
    prev_hash = load_last_event_hash()

    # Create a deterministic payload string for hashing (stable key order)
    payload = {
        "event_id": event_obj["event_id"],
        "time_utc": event_obj["time_utc"],
        "input_text_hash": event_obj["input_text_hash"],
        "claims": event_obj["claims"],
        "final_score": event_obj["final_score"],
        "verdict": event_obj["verdict"],
        "mode": event_obj["mode"],
        "reference_summary": event_obj.get("reference_summary", {}),
        "prev_hash": prev_hash,
    }
    payload_str = json.dumps(payload, sort_keys=True, separators=(",", ":"))

    event_hash = sha256_hex(payload_str)

    chain_record = dict(payload)
    chain_record["event_hash"] = event_hash

    # Append JSONL
    with open(CHAIN_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(chain_record) + "\n")

    return chain_record


# === Routes ===

@app.get("/")
def root():
    # Serve the UI from /static/index.html (your repo structure)
    return send_from_directory("static", "index.html")


@app.get("/health")
def health():
    return jsonify({
        "routes": ["/", "/health", "/verify", "/chain/last"],
        "service": "TruCite Backend",
        "status": "ok",
        "time_utc": utc_now_iso()
    })


@app.post("/verify")
def verify():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Missing 'text' field"}), 400

    # 1) Extract claims
    claims = extract_claims(text)

    # 2) Score with seed-corpus reference grounding (MVP)
    # Returns: final_score, verdict, reference_summary, confidence_curve
    result = score_with_seed_corpus(text, claims)

    final_score = int(result.get("final_score", 50))
    verdict = result.get("verdict", "Needs Review")
    reference_summary = result.get("reference_summary", {})
    confidence_curve = result.get("confidence_curve", [])

    # 3) Build event object (pre-hash)
    event_id = f"evt_{uuid.uuid4().hex[:10]}"
    input_text_hash = sha256_hex(text)

    event_obj = {
        "event_id": event_id,
        "time_utc": utc_now_iso(),
        "input_text_hash": input_text_hash,
        "claims": claims,
        "final_score": final_score,
        "verdict": verdict,
        "mode": result.get("mode", "claim_engine_v2_seed_grounding"),
        "reference_summary": reference_summary,
    }

    # 4) Append to integrity chain (adds prev_hash + event_hash)
    chain_record = append_chain_event(event_obj)

    # 5) Respond
    return jsonify({
        "event_id": chain_record["event_id"],
        "event_hash": chain_record["event_hash"],
        "prev_hash": chain_record["prev_hash"],
        "time_utc": chain_record["time_utc"],
        "claims": chain_record["claims"],
        "final_score": chain_record["final_score"],
        "verdict": chain_record["verdict"],
        "confidence_curve": confidence_curve,
        "explanation": "Claim Engine v2 + MVP Reference Grounding (seed corpus keyword match). Next: drift tracking + integrity chain persistence in DB.",
        "reference_summary": chain_record.get("reference_summary", {}),
        "mode": chain_record["mode"]
    })


@app.get("/chain/last")
def chain_last():
    """
    Returns the most recent chain record (or GENESIS state).
    """
    if not os.path.exists(CHAIN_PATH):
        return jsonify({"status": "empty", "last_event_hash": "GENESIS"})

    try:
        with open(CHAIN_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            if f.tell() == 0:
                return jsonify({"status": "empty", "last_event_hash": "GENESIS"})

            pos = f.tell() - 1
            while pos > 0:
                f.seek(pos)
                if f.read(1) == b"\n":
                    break
                pos -= 1
            f.seek(pos + 1)
            last_line = f.read().decode("utf-8").strip()
            obj = json.loads(last_line) if last_line else {}
            return jsonify({"status": "ok", "last": obj})
    except Exception as e:
        return jsonify({"status": "error", "error": str(e)}), 500


# Render/Gunicorn entrypoint expects "app"
# gunicorn app:app
if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
