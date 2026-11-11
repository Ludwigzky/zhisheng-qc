# -*- coding: utf-8 -*-
"""Check historical transcripts against isolated records, including access scope."""
from functools import wraps
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

from flask import Flask, session

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import db
import steel_recovery


class VoiceHistoryTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.conn = sqlite3.connect(str(Path(directory.name) / "history.db"))
        self.addCleanup(self.conn.close)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(db.SCHEMA)
        self.conn.execute("""INSERT INTO steel_records
            (id,owner_id,created_at,updated_at,status,data_json,evaluation_json,template_hash,snapshot)
            VALUES (33,1,'','','draft','{}','{}','',''),(34,2,'','','draft','{}','{}','','')""")
        app = Flask(__name__)
        app.secret_key = "temporary-test-session"

        def need_login(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if "user" not in session:
                    return "", 401
                return fn(*args, **kwargs)
            return wrapped

        steel_recovery.register(app, lambda: self.conn, lambda: session["user"], need_login)
        self.client = app.test_client()

    def login(self, uid, role="inspector"):
        with self.client.session_transaction() as sess:
            sess["user"] = {"id": uid, "role": role}

    def audit(self, rid, detail, action="steel_asr"):
        self.conn.execute("INSERT INTO audit(at,action,target,detail) VALUES(?,?,?,?)",
                          ("2026-11-10 17:00:00", action, "steel:%d" % rid, detail))

    def test_old_unknown_and_new_targeted_history_is_scoped_and_read_only(self):
        self.audit(33, json.dumps({"text": "原来的一段", "audio_id": "private.wav"}))
        self.audit(33, json.dumps({"text": "第二段", "current_key": "grade", "audio_id": "private2.wav"}))
        self.audit(33, json.dumps({"text": "第三段", "current_key": "foreign_key"}))
        self.audit(33, "invalid json")
        self.audit(34, json.dumps({"text": "另一份表的语音"}))
        self.audit(33, json.dumps({"text": "其他审计"}), "steel_save")
        changes = self.conn.total_changes
        self.login(1)
        result = self.client.get("/api/steel/records/33/voice-history")
        self.assertEqual(result.status_code, 200)
        self.assertIn("no-store", result.headers["Cache-Control"])
        expected = [{"at": "2026-11-10 17:00:00", "text": text, "field_label": label}
                    for text, label in [("第三段", None), ("第二段", "牌号"), ("原来的一段", None)]]
        self.assertEqual(result.get_json(), {"entries": expected})
        self.assertEqual(changes, self.conn.total_changes)

    def test_permissions_and_missing_record(self):
        path = "/api/steel/records/33/voice-history"
        self.assertEqual(self.client.get(path).status_code, 401)
        self.login(2)
        self.assertEqual(self.client.get(path).status_code, 403)
        self.login(3, "supervisor")
        self.assertEqual(self.client.get(path).get_json(), {"entries": []})
        self.assertEqual(self.client.get("/api/steel/records/999/voice-history").status_code, 404)

    def test_limit_and_order(self):
        for index in range(45):
            self.audit(33, json.dumps({"text": str(index)}))
        self.login(1)
        entries = self.client.get("/api/steel/records/33/voice-history").get_json()["entries"]
        self.assertEqual([item["text"] for item in entries], [str(i) for i in range(44, 14, -1)])


if __name__ == "__main__":
    unittest.main()
