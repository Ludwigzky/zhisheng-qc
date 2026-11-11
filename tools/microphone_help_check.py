# -*- coding: utf-8 -*-
"""Microphone help checks using an independent Flask app, SQLite and cert."""
from datetime import datetime, timedelta
from functools import wraps
import hashlib
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import unittest

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from flask import Flask, jsonify, session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import db
import microphone_help


class MicrophoneHelpChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "microphone-test.local")])
        now = datetime.utcnow()
        cls.cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
                    .public_key(key.public_key()).serial_number(x509.random_serial_number())
                    .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=1))
                    .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                    .sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qc-mic-help-")
        self.root = Path(self.temporary.name)
        (self.root / "certs").mkdir()
        (self.root / "certs" / "cert.pem").write_bytes(self.cert)
        # An intentionally invalid private-key sentinel must never be served.
        (self.root / "certs" / "key.pem").write_text("PRIVATE KEY SENTINEL DO NOT READ", encoding="ascii")
        self.connection = sqlite3.connect(str(self.root / "test.db"))
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(db.SCHEMA)
        self.app = Flask(__name__, static_folder=None)
        self.app.secret_key = "temporary-microphone-help-check"
        self.app.config.update(TESTING=True, JSON_AS_ASCII=False)

        def me():
            return {"id": 41, "name": "临时测试检验员"} if session.get("logged_in") else None

        def need_login(fn):
            @wraps(fn)
            def wrapped(*args, **kwargs):
                if not me():
                    return jsonify({"error": "未登录"}), 401
                return fn(*args, **kwargs)
            return wrapped

        microphone_help.register(self.app, self.root, lambda: self.connection, me, need_login)
        self.client = self.app.test_client()

    def tearDown(self):
        self.connection.close()
        self.temporary.cleanup()

    def login(self):
        with self.client.session_transaction() as state:
            state["logged_in"] = True

    def diagnostic(self):
        row = self.connection.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def test_public_certificate_download_is_exact_der_and_never_private_key(self):
        response = self.client.get("/api/asr/certificate?filename=key.pem&path=../certs/key.pem")
        expected = ssl.PEM_cert_to_DER_cert(self.cert.decode("ascii"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, expected)
        self.assertEqual(response.content_type, "application/pkix-cert")
        self.assertIn("qc-demo-ca.cer", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["Cache-Control"], "private, no-store")
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertNotIn(b"PRIVATE KEY", response.data)
        self.assertEqual(x509.load_der_x509_certificate(response.data).subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value, "microphone-test.local")

    def test_help_has_three_steps_and_public_metadata_only(self):
        response = self.client.get("/api/asr/microphone-help")
        data = response.get_json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(data["steps"]), 3)
        self.assertEqual(data["certificate"]["download_url"], "/api/asr/certificate")
        expected = hashlib.sha256(ssl.PEM_cert_to_DER_cert(self.cert.decode("ascii"))).hexdigest()
        self.assertEqual(data["certificate"]["sha256"], expected)
        self.assertNotIn(str(self.root), response.get_data(as_text=True))
        self.assertNotIn("key.pem", response.get_data(as_text=True))
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")

    def test_missing_or_malformed_cert_does_not_reveal_filesystem(self):
        path = self.root / "certs" / "cert.pem"
        for content in (None, b"PRIVATE KEY SENTINEL DO NOT READ", b"\xff\xfe"):
            with self.subTest(content=content):
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(content)
                response = self.client.get("/api/asr/certificate")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn(str(self.root), response.get_data(as_text=True))
                self.assertNotIn("PRIVATE KEY", response.get_data(as_text=True))
                help_data = self.client.get("/api/asr/microphone-help").get_json()["certificate"]
                self.assertFalse(help_data["available"])
                self.assertIsNone(help_data["download_url"])
                self.assertIsNone(help_data["sha256"])

    def test_report_needs_login_and_rejects_cross_origin(self):
        self.assertEqual(self.client.post("/api/asr/microphone-report", json={}).status_code, 401)
        self.assertIsNone(self.diagnostic())
        self.login()
        for origin in ("https://attacker.example", "null", "http://localhost@attacker.example", "http://localhost/path", "http://localhost?x=1"):
            with self.subTest(origin=origin):
                self.assertEqual(self.client.post("/api/asr/microphone-report", json={}, headers={"Origin": origin}).status_code, 403)
        self.assertIsNone(self.diagnostic())
        self.assertEqual(self.client.post("/api/asr/microphone-report", json={}, headers={"Origin": "http://localhost:80"}).status_code, 200)

    def test_report_is_bounded_allowlisted_json_and_uses_actual_request_origin(self):
        self.login()
        body = {"secure": True, "permission": "prompt", "audio": "suspended" * 20,
                "error": "permission-timeout" * 20, "phase": "permission" * 20,
                "ui_version": "v1" * 100, "origin": "https://attacker.invalid/leak", "user_agent": "forged",
                "text": "secret spoken text", "audio_blob": "PRIVATE AUDIO", "data": {"manufacturer": "private form"}}
        response = self.client.post("/api/asr/microphone-report", json=body,
                                    headers={"User-Agent": "actual-browser-" + "X" * 600, "Origin": "http://localhost"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"ok": True})
        audit = self.diagnostic()
        self.assertEqual(audit["action"], "mic_diagnostic")
        self.assertEqual(audit["actor_name"], "临时测试检验员")
        detail = json.loads(audit["detail"])
        self.assertEqual(set(detail), {"secure", "permission", "audio", "error", "phase", "ui_version", "user_agent", "origin"})
        self.assertEqual(detail["origin"], "http://localhost/")
        self.assertTrue(detail["user_agent"].startswith("actual-browser-"))
        for key, maximum in (("audio", 32), ("error", 80), ("phase", 32), ("ui_version", 40), ("user_agent", 400)):
            self.assertEqual(len(detail[key]), maximum)
        for secret in ("attacker", "PRIVATE AUDIO", "private form", "secret spoken text", "forged"):
            self.assertNotIn(secret, audit["detail"])

    def test_malformed_and_oversized_reports_do_not_write_or_render_html(self):
        self.login()
        for body in (None, [], "<script>alert(1)</script>", 12):
            with self.subTest(body=body):
                response = self.client.post("/api/asr/microphone-report", data=json.dumps(body), content_type="application/json")
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.content_type, "application/json")
                self.assertNotIn(b"<script>", response.data)
        self.assertEqual(self.client.post("/api/asr/microphone-report", json={"unused": "x" * 9000}).status_code, 413)
        self.assertIsNone(self.diagnostic())
        response = self.client.post("/api/asr/microphone-report", json={"secure": "true", "permission": {"any": "value"},
                                    "audio": {"private": "data"}, "error": ["private"], "phase": True, "ui_version": 1})
        self.assertEqual(response.status_code, 200)
        detail = json.loads(self.diagnostic()["detail"])
        self.assertIsNone(detail["secure"])
        self.assertEqual(detail["permission"], "unknown")
        for key in ("audio", "error", "phase", "ui_version"):
            self.assertEqual(detail[key], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
