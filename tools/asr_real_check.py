# -*- coding: utf-8 -*-
"""Exercise real native ASR -> upload -> parse -> confirmation on temporary data.

The bundled recording has no verified transcript, so this is a working-pipeline
check, not a claim about recognition accuracy. Never resets a running service.
"""
import argparse
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app as server
import asr_engine
import db
import judge
import seed
import test_accounts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wav", default=str(ROOT / "data/audio/f2014cc49006_dn.wav"))
    parser.add_argument("--current-key", default="diag_l2")
    parser.add_argument("--output", default=str(ROOT / "_probe/asr-real-check.json"))
    args = parser.parse_args()
    started = time.monotonic()
    engine = asr_engine.get_engine()
    initialization_ms = int((time.monotonic() - started) * 1000)
    if not engine.available or engine.name != "sherpa-onnx":
        raise RuntimeError("需要真实已安装的 sherpa-onnx 引擎: %s" % engine.info())
    original = {name: getattr(db, name) for name in ("DATA_DIR", "DB_PATH", "AUDIO_DIR", "REPORT_DIR")}
    grammar_dir = server.GRAMMAR_DIR
    try:
        payload = Path(args.wav).read_bytes()
        with tempfile.TemporaryDirectory(prefix="qc-real-asr-") as directory:
            db.DATA_DIR = directory
            db.DB_PATH = str(Path(directory) / "test.db")
            db.AUDIO_DIR = str(Path(directory) / "audio")
            db.REPORT_DIR = str(Path(directory) / "reports")
            server.GRAMMAR_DIR = str(Path(directory) / "grammar")
            db.init_db()
            c = db.connect()
            seed.seed_if_empty(c)
            test_accounts.install(c)
            task = db.row(c, "SELECT id FROM tasks WHERE seq=2 ORDER BY id LIMIT 1")
            # This fixture exercises the process-inspection schema independently.
            c.execute("UPDATE tasks SET predecessors='[]' WHERE id=?", (task["id"],))
            c.commit()
            c.close()
            with server.app.test_client() as client:
                test_accounts.login(client)
                claim = client.post("/api/tasks/%d/claim" % task["id"], json={"version":1})
                assert claim.status_code == 200, claim.get_json()
                status = client.get("/api/asr/status").get_json()
                assert status["available"] and status["engine"] == "sherpa-onnx"
                started = time.monotonic()
                response = client.post("/api/asr/audio", data={
                    "task_id": str(task["id"]), "current_key": args.current_key,
                    # Deliberately send an older saved setting. The backend must
                    # use supported dictation without flagging grammar failure.
                    "asr_mode": "grammar", "mode": "system", "denoise": "0",
                    "audio": (io.BytesIO(payload), "input.wav")})
                upload_ms = int((time.monotonic() - started) * 1000)
                result = response.get_json()
                assert response.status_code == 200, result
                assert result["text"] and result["engine"]["engine"] == "sherpa-onnx", result
                assert result["asr_mode"] == "dictation" and result["grammar"]["ok"], result
                assert result["requires_confirmation"] and result["confidence"] == 0, result
                parsed_response = client.post("/api/asr/parse", json={
                    "task_id": task["id"], "current_key": args.current_key,
                    "text": result["text"], "asr_confidence": result["confidence"]})
                assert parsed_response.status_code == 200
                parsed = parsed_response.get_json()
                assignments = [a for a in parsed["assignments"] if not a.get("derived")]
                assert assignments, parsed
                values = {a["key"]: a for a in assignments}
                judged = judge.judge(seed.TPL_IPQC, values)
                for row in judged["rows"]:
                    if row["key"] in values:
                        assert row["status"] == "pending", row
                snapshot = {"engine": status, "source": str(Path(args.wav).resolve()),
                            "reference_available": False, "initialization_ms": initialization_ms,
                            "upload_and_decode_ms": upload_ms, "result": result,
                            "assignments": assignments, "verification": "real_decode_upload_parse_pending_passed"}
                output = Path(args.output).resolve()
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
                print(json.dumps(snapshot, ensure_ascii=False, indent=2))
                print("PASS: real model, HTTP upload, parsed fields and required confirmation; temporary database only")
    finally:
        for name, value in original.items():
            setattr(db, name, value)
        server.GRAMMAR_DIR = grammar_dir
        engine.close()


if __name__ == "__main__":
    main()
