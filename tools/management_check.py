"""Management permissions, provenance, filters and exact counters on disposable data."""
import csv
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["ASR_ENGINE"] = "stub"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from werkzeug.security import generate_password_hash
import app as server
import db
import identity
import judge
import management
import seed
import steel
import steel_report

if "management" not in server.app.blueprints:
    management.register(server.app, server.conn, server.me, server.need_role)


class ManagementChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.password = "isolated-manager-password"
        cls.hashed = generate_password_hash(cls.password, method=identity.PASSWORD_METHOD)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="qc-manager-")
        self.addCleanup(tmp.cleanup)
        paths = patch.multiple(db, DATA_DIR=tmp.name, DB_PATH=str(Path(tmp.name) / "check.db"),
                               AUDIO_DIR=str(Path(tmp.name) / "audio"), REPORT_DIR=str(Path(tmp.name) / "reports"))
        paths.start()
        self.addCleanup(paths.stop)
        db.init_db()
        self.c = db.connect()
        self.addCleanup(self.c.close)
        seed.seed_if_empty(self.c)
        self.ids = {}
        with self.c:
            for number, role, dept in (("ADMIN", "admin", "管理"), ("CHIEF-A", "supervisor", "A"),
                                       ("CHIEF-B", "supervisor", "B"), ("WORKER-A", "inspector", "A"),
                                       ("WORKER-B", "inspector", "B"), ("TECH-A", "process", "A")):
                self.ids[number] = identity._new_user(self.c, number, number, dept, role, self.hashed, role == "admin")
            self.c.execute("UPDATE plans SET workgroup='A'")
        self.clients = {}
        for number in self.ids:
            client = server.app.test_client()
            login = client.post("/api/login", json={"emp_no": number, "password": self.password})
            self.assertEqual(login.status_code, 200)
            self.clients[number] = client
        self.admin = self.clients["ADMIN"]
        self.chief = self.clients["CHIEF-A"]

    def steel_record(self, state="pass", owner="WORKER-A", parent=None, day="2026-11-01", **fields):
        data = steel.normalize(steel.employee_example())
        data.update(is_example=False, inspection_date=day, document_no="REC-" + str(self.c.execute("SELECT count(*) FROM steel_records").fetchone()[0]))
        data.update(fields)
        if state == "fail":
            data["values"]["surface"]["value"] = "不合格"
            data.update(qualified_quantity=0, rejected_quantity=1)
        if state == "draft":
            data["values"] = {}
        final = state in ("pass", "fail")
        inspector = owner if final else ""
        evaluation = steel.evaluate(data)
        self.assertEqual(evaluation["overall"], state if final else "pending")
        standard = self.c.execute("SELECT id FROM steel_standards ORDER BY id LIMIT 1").fetchone()[0]
        with self.c:
            rid = db.insert(self.c, "steel_records", {
                "owner_id": self.ids[owner], "created_at": day + " 08:00:00", "updated_at": day + " 09:00:00",
                "version": 2 if final else 1, "status": "final" if final else "draft", "data_json": json.dumps(data),
                "evaluation_json": json.dumps(evaluation), "template_hash": steel_report.template_hash(),
                "snapshot": steel.snapshot({"data": data, "inspector": inspector}, steel_report.template_hash()),
                "inspector_name": inspector, "signed_at": day + " 09:00:00" if final else None,
                "parent_id": parent, "standard_id": standard})
            db.insert(self.c, "audit", {"at": day + " 08:01:00", "actor_id": self.ids[owner], "actor_name": owner,
                      "action": "steel_finalize" if final else "steel_save", "target": "steel:%d" % rid,
                      "detail": json.dumps({"test": data["document_no"]})})
        return rid

    def task_round(self, state="pass", round_no=1, day="2026-11-01"):
        task = db.row(self.c, "SELECT * FROM tasks ORDER BY id LIMIT 1")
        fields = seed.TPL_IQC["sections"][0]["fields"]
        values = {field["key"]: {"source": "manual", "value": value} for field, value in
                  zip(fields, [20 if state != "fail" else 21, 200, "HEAT-A", "齐全", "合格"])}
        if state == "pending":
            values = {}
        evaluated = judge.judge(seed.TPL_IQC, values)
        self.assertEqual(evaluated["overall"], state)
        with self.c:
            rid = db.insert(self.c, "records", {
                "task_id": task["id"], "round": round_no, "operator_id": self.ids["WORKER-A"],
                "created_at": day + " 10:00:00", "transcript": "", "engine": "manual", "audio_id": None,
                "template_id": task["template_id"], "template_revision": task["template_revision"],
                "std_version": seed.TPL_IQC["std_version"], "values_json": json.dumps(values),
                "judge_json": json.dumps(evaluated), "overall": state, "snapshot": evaluated["snapshot"]})
            self.c.execute("UPDATE tasks SET latest_record_id=? WHERE id=?", (rid, task["id"]))
            db.insert(self.c, "audit", {"at": day + " 10:00:00", "actor_id": self.ids["WORKER-A"],
                      "actor_name": "WORKER-A", "action": "submit_record", "target": "task:%d" % task["id"],
                      "detail": json.dumps({"round": round_no})})
        return rid

    def get(self, client, suffix="records", query=""):
        response = client.get("/api/management/" + suffix + ("?" + query if query else ""))
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def test_roles_and_department_scope_cover_all_endpoints(self):
        a = self.steel_record(document_no="ONLY-A")
        b = self.steel_record(owner="WORKER-B", document_no="ONLY-B")
        endpoints = ("overview", "records", "options", "export.csv", "records/steel/%d" % a)
        anonymous = server.app.test_client()
        for endpoint in endpoints:
            self.assertEqual(anonymous.get("/api/management/" + endpoint).status_code, 401)
            for number in ("WORKER-A", "TECH-A"):
                self.assertEqual(self.clients[number].get("/api/management/" + endpoint).status_code, 403)
        self.assertEqual([r["document_no"] for r in self.get(self.chief)["items"]], ["ONLY-A"])
        self.assertEqual(self.chief.get("/api/management/records/steel/%d" % b).status_code, 404)
        self.assertNotIn("ONLY-B", self.chief.get("/api/management/export.csv").get_data(as_text=True))
        self.assertEqual(self.get(self.admin)["total"], 2)
        self.assertEqual(self.get(self.chief, query="dept=B")["total"], 0)
        self.assertTrue(all(p["dept"] == "A" for p in self.get(self.chief, "options")["people"]))

    def test_all_rows_enter_statistics_and_csv_before_pagination(self):
        for _ in range(105):
            self.steel_record()
        example = steel.employee_example()
        self.steel_record(is_example=True)
        listing = self.get(self.chief, query="page=2&page_size=20")
        self.assertEqual((listing["total"], listing["pages"], len(listing["items"])), (105, 6, 20))
        summary = self.get(self.chief, "overview", "page=2&page_size=20")
        self.assertEqual(summary["metrics"]["final_count"], 105)
        self.assertEqual(summary["metrics"]["first_pass_rate"], 100)
        rows = list(csv.reader(io.StringIO(self.chief.get("/api/management/export.csv?page=2").get_data(as_text=True).lstrip("\ufeff"))))
        self.assertEqual(len(rows), 106)
        self.assertNotIn("_raw", str(listing))
        self.assertNotIn("_evaluation", str(listing))

    def test_first_inspection_and_retest_are_not_overwritten(self):
        root = self.steel_record("fail")
        child = self.steel_record("draft", parent=root, day="2026-11-02")
        retry = self.steel_record("pass", parent=child, day="2026-11-03")
        self.steel_record("pass")
        summary = self.get(self.chief, "overview")["metrics"]
        self.assertEqual((summary["final_count"], summary["pass_count"], summary["fail_count"], summary["draft_count"]), (3, 2, 1, 1))
        self.assertEqual((summary["first_inspection_count"], summary["first_pass_rate"], summary["retest_count"]), (2, 50, 1))
        for state, count in (("first", 2), ("retest", 1), ("final", 3), ("unfinished", 1)):
            self.assertEqual(self.get(self.chief, query="state=" + state)["total"], count)
        detail = self.get(self.chief, "records/steel/%d" % retry)
        self.assertEqual([r["id"] for r in detail["chain"]], [root, child, retry])
        self.assertEqual(detail["record"]["root_id"], root)
        self.assertEqual(detail["timeline"][0]["detail"], {"test": "REC-2"})

    def test_search_dates_people_state_and_defect_drill_down(self):
        self.steel_record("fail", day="2026-11-03", document_no="D%_001", manufacturer="甲厂", heat_no="HEAT-9")
        self.steel_record("pass", day="2026-11-04", document_no="D123001")
        self.assertEqual(self.get(self.chief, query="q=%25_")["total"], 1)
        self.assertEqual(self.get(self.chief, query="q=HEAT-9")["total"], 1)
        self.assertEqual(self.get(self.chief, query="date_from=2026-11-04&date_to=2026-11-04")["total"], 1)
        self.assertEqual(self.get(self.chief, query="owner_id=%d&state=fail" % self.ids["WORKER-A"])["total"], 1)
        failed = self.get(self.chief, "overview")["defects"]
        self.assertEqual(failed, [{"label": "表面质量", "count": 1}])
        self.assertEqual(self.get(self.chief, query="defect=表面质量")["total"], 1)
        self.assertEqual(self.get(self.chief, query="defect=厚度")["total"], 0)
        for query in ("date_from=2026-13-01", "date_from=2026-11-05&date_to=2026-11-01", "page=0", "page_size=101",
                      "state=unknown", "kind=unknown", "owner_id=zero"):
            self.assertEqual(self.chief.get("/api/management/records?" + query).status_code, 400)

    def test_task_rounds_and_historical_readings_preserve_their_own_values(self):
        pending = self.task_round("pending", 1)
        failed = self.task_round("fail", 2, "2026-11-02")
        passed = self.task_round("pass", 3, "2026-11-03")
        metrics = self.get(self.chief, "overview", "kind=task")["metrics"]
        self.assertEqual((metrics["pending_count"], metrics["final_count"], metrics["first_inspection_count"],
                          metrics["first_pass_rate"], metrics["retest_count"]), (1, 2, 1, 0, 1))
        history = self.get(self.chief, "records/task/%d" % failed)
        self.assertEqual(history["readings"][0]["value"], 21)
        self.assertEqual([r["id"] for r in history["chain"]], [pending, failed, passed])
        self.assertEqual(len(history["timeline"]), 1)
        self.assertEqual(history["timeline"][0]["detail"]["round"], 2)
        self.assertEqual(history["artifacts"], [])
        self.assertEqual(self.get(self.clients["CHIEF-B"], query="kind=task")["total"], 0)
        with self.c:
            self.c.execute("UPDATE users SET dept='B' WHERE id=?", (self.ids["WORKER-A"],))
        self.assertEqual(self.get(self.chief, query="kind=task&dept=A")["total"], 3)

    def test_csv_formula_payloads_are_literal_and_bom_is_present(self):
        self.steel_record(document_no="=HYPERLINK(1)", manufacturer=" +CMD", heat_no="@SUM(1)")
        response = self.chief.get("/api/management/export.csv")
        self.assertTrue(response.data.startswith(b"\xef\xbb\xbf"))
        self.assertIn("nosniff", response.headers["X-Content-Type-Options"])
        fields = list(csv.reader(io.StringIO(response.get_data(as_text=True).lstrip("\ufeff"))))[1]
        self.assertIn("'=HYPERLINK(1)", fields)
        self.assertIn("' +CMD", fields)
        self.assertIn("'@SUM(1)", fields)

    def test_cross_department_parent_is_not_exposed_or_counted_as_first(self):
        parent = self.steel_record("fail", owner="WORKER-B", document_no="HIDDEN-PARENT")
        child = self.steel_record("pass", parent=parent)
        detail = self.get(self.chief, "records/steel/%d" % child)
        self.assertEqual(len(detail["chain"]), 1)
        self.assertIsNone(detail["record"]["parent_id"])
        self.assertTrue(detail["record"]["lineage_restricted"])
        self.assertFalse(detail["record"]["first_inspection"])
        self.assertNotIn("HIDDEN-PARENT", str(detail))
        self.assertIsNone(self.get(self.chief, "overview")["metrics"]["first_pass_rate"])

    def test_approved_deviation_is_visible_and_read_queries_do_not_modify_audits(self):
        rid = self.steel_record("pass")
        standard = self.c.execute("SELECT standard_id FROM steel_records WHERE id=?", (rid,)).fetchone()[0]
        with self.c:
            db.insert(self.c, "steel_deviations", {
                "record_id": rid, "standard_id": standard, "applicant_id": self.ids["WORKER-A"],
                "reason": "测试技术协议补充条款", "limits_json": json.dumps({"thickness_upper": 0.4}),
                "created_at": "2026-11-01 08:00:00", "status": "approved", "reviewer_id": self.ids["TECH-A"],
                "reviewed_at": "2026-11-01 08:01:00", "decision": "依据已核对"})
        before = self.c.execute("SELECT count(*) FROM audit").fetchone()[0]
        detail = self.get(self.chief, "records/steel/%d" % rid)
        approved = detail["record"]["approved_deviation"]
        self.assertEqual((approved["reviewer"], approved["reason"], approved["limits"]["thickness_upper"]),
                         ("TECH-A", "测试技术协议补充条款", 0.4))
        self.assertEqual(detail["record"]["standard"]["criteria"]["thickness_upper"], 0.29)
        self.get(self.chief, "options")
        self.get(self.chief, "overview")
        self.chief.get("/api/management/export.csv").get_data()
        self.assertEqual(self.c.execute("SELECT count(*) FROM audit").fetchone()[0], before)

    def test_unsigned_legacy_final_does_not_enter_confirmed_statistics(self):
        rid = self.steel_record("pass")
        with self.c:
            self.c.execute("UPDATE steel_records SET inspector_name='',signed_at=NULL WHERE id=?", (rid,))
        summary = self.get(self.chief, "overview")["metrics"]
        self.assertEqual((summary["final_count"], summary["pending_count"], summary["pass_rate"]), (0, 1, None))
        detail = self.get(self.chief, "records/steel/%d" % rid)
        self.assertFalse(any(a["kind"] == "xlsx" for a in detail["artifacts"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
