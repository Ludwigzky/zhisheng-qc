# -*- coding: utf-8 -*-
"""Account bootstrap, invitation and revocation regression tests on an isolated database."""
import hashlib
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, g, jsonify

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import db
import identity


class IdentityChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = str(Path(tmp.name) / "test.db")
        with sqlite3.connect(self.path) as c:
            c.executescript(db.SCHEMA)
            c.execute("INSERT INTO users(emp_no,name,role,dept) VALUES('QC1024','历史员工','inspector','旧组')")
        self.app = Flask(__name__)
        self.app.secret_key = "isolated-test-key"
        self.app.testing = True
        # This Flask 1 / Werkzeug 2 test client rebuilds requests without
        # REMOTE_ADDR. Supply the actual transport peer in this test WSGI wrapper.
        application = self.app.wsgi_app
        def with_test_peer(environ, start_response):
            environ["REMOTE_ADDR"] = environ.pop("HTTP_X_TEST_PEER", "127.0.0.1")
            return application(environ, start_response)
        self.app.wsgi_app = with_test_peer

        def conn():
            if "conn" not in g:
                g.conn = sqlite3.connect(self.path)
                g.conn.row_factory = sqlite3.Row
            return g.conn

        @self.app.teardown_appcontext
        def close(err):
            c = g.pop("conn", None)
            if c:
                c.close()

        identity.register(self.app, conn)

        @self.app.route("/me")
        def me():
            return jsonify(user=identity.current_user(conn()))

        self.admin = self.app.test_client()
        self.admin_data = {"emp_no": "ADMIN-A", "name": "本机管理员", "dept": "甲组", "password": "example admin password"}
        self.employee = {"emp_no": "WORKER-01", "name": "现场员工", "dept": "乙组", "role": "inspector"}

    def setup_admin(self):
        result = self.admin.post("/api/auth/setup", json=self.admin_data)
        self.assertEqual(result.status_code, 201, result.get_json())
        self.assertEqual(result.get_json()["user"]["role"], "admin")
        return result

    def invite(self):
        response = self.admin.post("/api/admin/invites", json=self.employee)
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()

    def test_setup_requires_real_loopback_and_rejects_external_origin_or_proxy(self):
        for args in ({"headers":{"X-Test-Peer":"192.168.1.8"}},
                     {"base_url":"https://192.168.1.3"},
                     {"headers":{"X-Forwarded-For":"192.168.1.8"}},
                     {"headers":{"Origin":"https://other.invalid"}}):
            self.assertEqual(self.admin.post("/api/auth/setup", json=self.admin_data, **args).status_code, 403)
        self.assertEqual(self.admin.post("/api/auth/setup", data=self.admin_data).status_code, 415)
        self.setup_admin()
        self.assertEqual(self.admin.post("/api/auth/setup", json=self.admin_data).status_code,409)
        state = self.admin.get("/api/auth/setup-state").get_json()
        self.assertFalse(state["setup_required"])
        self.assertTrue(state["local_setup_allowed"])

    def test_historical_user_cannot_login_and_dual_issuance_invalidates_old_code(self):
        self.setup_admin()
        guest = self.app.test_client()
        with guest.session_transaction() as session:
            session["uid"] = 1
        self.assertIsNone(guest.get("/me").get_json()["user"])
        self.assertEqual(guest.post("/api/login", json={"emp_no":"QC1024"}).status_code, 401)
        self.assertEqual(guest.post("/api/login", json={"emp_no":"QC1024","password":"unknown legacy"}).status_code, 401)
        self.assertEqual(guest.get("/api/admin/accounts").status_code, 401)
        a, b = self.invite(), self.invite()
        for wrong in (a["code"], "invalid-code"):
            self.assertEqual(guest.post("/api/auth/register", json={"code":wrong,"password":"a real password"}).status_code, 400)
        self.assertEqual(len(self.admin.get("/api/admin/accounts").get_json()["invites"]), 1)
        body = {"code":b["code"],"password":"my passphrase","role":"admin","dept":"恶意替换","name":"假姓名"}
        result = guest.post("/api/auth/register", json=body)
        self.assertEqual(result.status_code,201)
        user = result.get_json()["user"]
        self.assertEqual((user["role"],user["dept"],user["name"]),("inspector","乙组","现场员工"))
        self.assertNotIn("password_hash", user)
        self.assertEqual(guest.post("/api/auth/register", json=body).status_code,400)
        self.assertEqual(guest.post("/api/admin/invites",json=self.employee).status_code,403)
        listing = self.admin.get("/api/admin/accounts").get_json()["users"]
        self.assertEqual([u["emp_no"] for u in listing], ["ADMIN-A","WORKER-01"])
        with sqlite3.connect(self.path) as c:
            self.assertTrue(c.execute("SELECT password_hash FROM user_credentials WHERE user_id=?", (user["id"],)).fetchone()[0].startswith("pbkdf2:"))
            self.assertEqual(c.execute("SELECT code_hash FROM auth_invites WHERE id=?", (b["id"],)).fetchone()[0],
                             hashlib.sha256(b["code"].encode()).hexdigest())

    def test_double_redemption_is_atomic(self):
        self.setup_admin()
        code = self.invite()["code"]
        gate = threading.Barrier(2)
        def claim(n):
            client = self.app.test_client()
            gate.wait(timeout=5)
            return client.post("/api/auth/register",json={"code":code,"password":"our own new password"}).status_code
        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = sorted(pool.map(claim, range(2)))
        self.assertEqual(statuses,[201,400])

    def test_disable_reset_and_logout_revoke_every_affected_session(self):
        self.setup_admin()
        c = self.app.test_client()
        invitation = self.invite()
        uid = c.post("/api/auth/register",json={"code":invitation["code"],"password":"original passphrase"}).get_json()["user"]["id"]
        account_path = "/api/admin/accounts/%s/" % uid
        second = self.app.test_client()
        login = dict(emp_no="WORKER-01",password="original passphrase")
        self.assertEqual(second.post("/api/login",json=login).status_code,200)
        self.assertEqual(self.admin.post(account_path+"disable",json={}).status_code,200)
        for client in (c,second):
            self.assertIsNone(client.get("/me").get_json()["user"])
        self.assertEqual(c.post("/api/login",json=login).status_code,401)
        self.admin.post(account_path+"enable",json={})
        self.assertEqual(c.post("/api/login",json=login).status_code,200)
        reset = self.admin.post(account_path+"reset",json={}).get_json()
        self.assertEqual(c.post("/api/login",json=login).status_code,401)
        self.assertEqual(c.post("/api/auth/register",json={"code":reset["code"],"password":"replacement passphrase"}).status_code,201)
        self.assertEqual(c.post("/api/login",json=login).status_code,401)
        with c.session_transaction() as active:
            old = active["auth_token"]
        c.post("/api/logout",json={})
        with c.session_transaction() as replay:
            replay["auth_token"] = old
        self.assertIsNone(c.get("/me").get_json()["user"])

    def test_expired_or_revoked_invites_and_rate_limit(self):
        self.setup_admin()
        guest=self.app.test_client()
        denied = self.admin.post("/api/admin/invites", json=dict(self.employee,role="admin"))
        self.assertEqual(denied.status_code,400)
        for invalidate in ("expire","revoke"):
            invite=self.invite()
            if invalidate=="expire":
                with sqlite3.connect(self.path) as c:
                    c.execute("UPDATE auth_invites SET expires_at=1 WHERE id=?", (invite["id"],))
            else:
                self.admin.post("/api/admin/invites/%s/revoke"%invite["id"],json={})
            self.assertEqual(guest.post("/api/auth/register",json={"code":invite["code"],"password":"phone password"}).status_code,400)
        for _ in range(8):
            self.assertEqual(guest.post("/api/login",json={"emp_no":"QC1024"}).status_code,401)
        self.assertEqual(guest.post("/api/login",json={"emp_no":"QC1024","password":"guess"}).status_code,429)


if __name__ == "__main__":
    unittest.main(verbosity=2)
