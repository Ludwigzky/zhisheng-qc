"""Regression checks for the signed workbook, range download, historical register and recovery."""
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from flask import Flask, session

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"backend"))
import db
import identity
import steel
import steel_archive as archive
import steel_archive_admin as admin
import steel_report
import steel_routes
import steel_standards


class ArchiveChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)/"project"
        folder = self.root/"data"
        folder.mkdir(parents=True)
        self.c = sqlite3.connect(str(folder/"demo.db"))
        self.addCleanup(self.c.close)
        self.c.row_factory = sqlite3.Row
        self.c.execute("PRAGMA foreign_keys=ON")
        self.c.executescript(db.SCHEMA)
        steel_standards.init_schema(self.c)
        archive.ensure_schema(self.c)
        self.ids = {}
        for name, role, team in (("A","inspector","一班"),("B","inspector","二班"),("S","supervisor","一班")):
            uid = db.insert(self.c,"users",{"emp_no":name,"name":name,"role":role,"dept":team})
            self.c.execute("INSERT INTO user_credentials(user_id,password_hash) VALUES(?,?)",(uid,"test-fixture"))
            self.ids[name] = uid
        self.c.commit()
        app = Flask(__name__)
        app.testing = True
        app.secret_key = "isolated-archive-check"
        def actor():
            return identity.active_user(self.c,session.get("uid"))
        from functools import wraps
        def need_login(fn):
            @wraps(fn)
            def logged(*args, **kwargs):
                return fn(*args,**kwargs) if actor() else ({"error":"login"},401)
            return logged
        steel_routes.register(app,lambda:self.c,actor,need_login,lambda *_:{})
        self.app = app
        self.client = self.login("A")

    def login(self, name):
        client = self.app.test_client()
        with client.session_transaction() as sess:
            sess["uid"] = self.ids[name]
        return client

    def draft(self, number="ARCHIVE-01", client=None):
        data = steel.employee_example()
        data.update(document_no=number, is_example=False, heat_no="HEAT-999")
        res = (client or self.client).post("/api/steel/records",json={"data":data})
        self.assertEqual(res.status_code,201,res.get_json())
        return res.get_json()["record"]

    def sign(self, record):
        return self.client.post("/api/steel/records/%d/finalize"%record["id"],
            json={"version":record["version"],"data":record["data"]})

    def test_signed_bytes_preview_and_range_survive_template_replacement(self):
        record = self.sign(self.draft()).get_json()["record"]
        path = "/api/steel/records/%d" % record["id"]
        origin, contents, checksum = self.c.execute("SELECT origin,xlsx,xlsx_sha256 FROM steel_archives").fetchone()
        self.assertEqual((origin,archive.digest(contents)),("signed",checksum))
        self.assertIn(b"ARCHIVE-01",zipfile.ZipFile(io.BytesIO(contents)).read("xl/worksheets/sheet1.xml"))
        with patch.object(steel_report,"TEMPLATE",self.root/"unavailable.xlsx"), patch.object(steel_report,"build_xlsx",side_effect=AssertionError):
            direct = self.client.get(path+"/xlsx")
            self.assertEqual(direct.data,contents)
            self.assertIn(b"ARCHIVE-01",self.client.get(path+"/preview").data)
            signed_url = self.client.post(path+"/download-link").get_json()["url"]
            guest = self.app.test_client()
            res = guest.get(signed_url,headers={"Range":"bytes=0-55"})
            self.assertEqual((res.status_code,res.data),(206,contents[:56]))
            self.c.execute("UPDATE user_credentials SET enabled=0 WHERE user_id=?",(self.ids["A"],))
            self.c.commit()
            self.assertEqual(guest.get(signed_url).status_code,403)

    def test_archive_failure_rolls_back_signature_and_followup(self):
        record = self.draft()
        record["data"]["values"]["surface"]["value"] = "不合格"
        record["data"].update(qualified_quantity=0,rejected_quantity=1)
        with patch.object(steel_report,"build_xlsx",side_effect=ValueError("template unavailable")):
            self.assertEqual(self.sign(record).status_code,409)
        self.assertEqual(tuple(self.c.execute("SELECT status,version FROM steel_records").fetchone()),("draft",1))
        self.assertEqual(self.c.execute("SELECT count(*) FROM steel_records").fetchone()[0],1)
        self.assertEqual(self.c.execute("SELECT count(*) FROM steel_archives").fetchone()[0],0)

    def test_legacy_snapshot_backfill_and_integrity_guard(self):
        record = self.sign(self.draft()).get_json()["record"]
        rid = record["id"]
        # Simulate the database version that recorded the signature but had no archive table.
        self.c.execute("DELETE FROM steel_archives")
        self.c.commit()
        with patch.object(steel_report,"TEMPLATE",self.root/"other.xlsx"):
            blob = archive.artifact(self.c,record,"xlsx")
        self.assertEqual(self.c.execute("SELECT origin FROM steel_archives").fetchone()[0],"legacy")
        self.c.execute("UPDATE steel_archives SET xlsx=? WHERE record_id=?",(blob+b"bad",rid))
        self.c.commit()
        self.assertEqual(self.client.get("/api/steel/records/%d/xlsx"%rid).status_code,409)
        self.c.execute("DELETE FROM steel_archives")
        self.c.execute("DELETE FROM steel_archive_templates")
        self.c.commit()
        with patch.object(steel_report,"TEMPLATE",self.root/"other.xlsx"):
            self.assertEqual(self.client.get("/api/steel/records/%d/xlsx"%rid).status_code,409)
        self.assertEqual(self.c.execute("SELECT count(*) FROM steel_archives").fetchone()[0],0)

    def test_scoped_search_finds_old_record_without_leaking_other_teams(self):
        final = self.sign(self.draft("FIRST-BATCH")).get_json()["record"]
        self.draft("PRIVATE-OTHER",self.login("B"))
        base = self.draft("later")
        # Insert 102 newer drafts while keeping the signed record the oldest.
        original = db.row(self.c,"SELECT * FROM steel_records WHERE id=?",(base["id"],))
        original.pop("id")
        for _ in range(102):
            db.insert(self.c,"steel_records",original)
        self.c.commit()
        view = self.login("S").get("/api/steel/records?page_size=2&page=999").get_json()
        self.assertEqual((view["page"],view["total"]),(52,104))
        self.assertEqual(view["records"][-1]["id"],final["id"])
        result = self.login("S").get("/api/steel/records?q=HEAT-999&status=pass&from=2026-03-24&to=2026-03-24").get_json()
        self.assertEqual([r["id"] for r in result["records"]],[final["id"]])
        self.assertEqual(self.client.get("/api/steel/records?q=PRIVATE-OTHER").get_json()["total"],0)
        self.assertEqual(self.login("B").post("/api/steel/records/%d/download-link"%final["id"]).status_code,404)
        self.assertEqual(self.client.get("/api/steel/records?page=0").status_code,400)

    def test_ledger_direct_files_and_signed_links_respect_current_scope(self):
        record = self.sign(self.draft()).get_json()["record"]
        path = "/api/steel/records/%d" % record["id"]
        supervisor = self.login("S")
        url = supervisor.post(path+"/download-link").get_json()["url"]
        self.assertEqual(self.app.test_client().get(url).status_code,200)
        self.c.execute("UPDATE users SET dept='二班' WHERE id=?", (self.ids["S"],))
        self.c.commit()
        self.assertEqual(supervisor.get("/api/steel/records").get_json()["total"],0)
        self.assertEqual(supervisor.get(path+"/xlsx").status_code,404)
        self.assertEqual(self.app.test_client().get(url).status_code,403)
        self.c.execute("UPDATE users SET role='process',dept='一班' WHERE id=?", (self.ids["S"],))
        self.c.commit()
        self.assertEqual(supervisor.get("/api/steel/records").get_json()["total"],0)
        for suffix in ("","/preview","/xlsx"):
            self.assertEqual(supervisor.get(path+suffix).status_code,404)
        self.assertEqual(supervisor.post(path+"/download-link").status_code,404)
        self.assertEqual(self.app.test_client().get(url).status_code,403)
        # An old actor ID is not a new account; this mirrors the now-disabled
        # demo records with no user_credentials row after the schema migration.
        self.c.execute("DELETE FROM user_credentials WHERE user_id=?", (self.ids["S"],))
        self.c.commit()
        self.assertEqual(supervisor.get("/api/steel/records").status_code,401)
        self.assertEqual(supervisor.post(path+"/download-link").status_code,401)
        self.assertEqual(self.app.test_client().get(url).status_code,403)

    def test_verified_backup_restores_signed_bytes_in_a_new_directory(self):
        self.sign(self.draft())
        audio = self.root/"data"/"audio"/"sample.wav"
        audio.parent.mkdir()
        audio.write_bytes(b"archived-evidence")
        saved, recovered = self.root.parent/"saved", self.root.parent/"recovered"
        admin.backup(self.root,saved)
        admin.restore(saved,recovered)
        self.assertEqual((recovered/"data/audio/sample.wav").read_bytes(),audio.read_bytes())
        actual = admin.db_connection(recovered/"data/demo.db","ro")
        try:
            self.assertEqual(actual.execute("SELECT xlsx FROM steel_archives").fetchone()[0],
                             self.c.execute("SELECT xlsx FROM steel_archives").fetchone()[0])
        finally:
            actual.close()
        with self.assertRaises(ValueError):
            admin.restore(saved,recovered)
        (saved/"data/audio/sample.wav").write_bytes(b"damaged")
        with self.assertRaises(ValueError):
            admin.verify(saved)


if __name__ == "__main__":
    unittest.main(verbosity=2)
