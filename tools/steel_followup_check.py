# -*- coding: utf-8 -*-
"""Isolated regressions for the final verdict and linked reinspection workflow.

Only temporary SQLite files and in-memory XLSX packages are used; no live server,
project database, original template, or delivered workbook is modified.
"""
import copy
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
import io
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from urllib.parse import urljoin
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

from flask import Flask, g, jsonify

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import db
import steel
import steel_report
import steel_routes
import steel_standards
import steel_archive
import identity
import workflow
import test_accounts

NS = steel_report.NS


def final_record(data):
    return {"data": steel.normalize(data), "status": "final", "inspector_name": "本次检验员",
            "template_hash": steel_report.template_hash(), "evaluation": {"overall": "pass"}}


def sheet_cells(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as book:
        sheet = ET.fromstring(book.read("xl/worksheets/sheet1.xml"))
        cells = {node.get("r"): "".join(node.find("s:is", NS).itertext())
                 for node in sheet.findall("s:sheetData/s:row/s:c[@t='inlineStr']", NS)}
    return sheet, cells


def failed_data():
    data = steel.employee_example()
    data.update(qualified_quantity=0, rejected_quantity=1)
    data["values"]["surface"]["value"] = "不合格"
    data["values"]["width"].update(source="asr", confidence=0, confirmed=True, raw="宽度一千八百一十一")
    data["extra_rows"] = [{"name": "长度", "requirement": "6000±5", "result": "6010", "verdict": "不合格"}]
    data["evidence"] = [{"text": "表面不合格", "audio_id": "parent.wav", "at": "历史录音时间"}]
    return data


class ExportChecks(unittest.TestCase):
    def test_final_verdict_in_left_merged_blank_covers_every_result_source(self):
        cases = [("pass", steel.employee_example(), "合格")]
        for key in ("surface", "cert_provided", "thickness", "width", "extra", "counts"):
            data = steel.employee_example()
            data.update(qualified_quantity=0, rejected_quantity=1)
            if key in ("surface", "cert_provided"):
                data["values"][key]["value"] = "不合格"
            elif key in ("thickness", "width"):
                data["values"][key]["value"] = [6.1 if key == "thickness" else 1826]
            elif key == "extra":
                data["extra_rows"] = [{"name": "长度", "requirement": "6000", "result": "6010", "verdict": "不合格"}]
            cases.append((key, data, "不合格"))
        for name, data, verdict in cases:
            with self.subTest(name=name):
                record = final_record(data)
                self.assertTrue(steel.evaluate(record["data"])["can_finalize"])
                sheet, cells = sheet_cells(steel_report.build_xlsx(record))
                self.assertEqual(cells["B13"], verdict)
                self.assertIn("100%检验", cells["F13"])
                self.assertTrue(all(node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
                                    for node in sheet.findall("s:sheetData/s:row/s:c[@r='F13']/s:is/s:r/s:t", NS)))
                self.assertIsNotNone(sheet.find("s:mergeCells/s:mergeCell[@ref='B13:E13']", NS))
                self.assertIsNone(sheet.find("s:sheetData/s:row/s:c[@r='B13']/s:is/s:r", NS))

    def test_sampling_detail_moves_right_and_preserves_original_native_parts(self):
        original = steel_report.TEMPLATE.read_bytes()
        for bad in (0, 1):
            data = steel.employee_example()
            data.update(inspection_mode="sampling", supply_quantity=10, inspected_quantity=5,
                        qualified_quantity=5-bad, rejected_quantity=bad, disposition="加倍抽检" if bad else "")
            payload = steel_report.build_xlsx(final_record(data))
            sheet, cells = sheet_cells(payload)
            self.assertEqual(cells["B13"], "不合格" if bad else "合格")
            self.assertIn("抽检", cells["F13"])
            self.assertIn("原表备注抽样规则", cells["F13"])
            self.assertNotIn("100%检验", cells["F13"])
            self.assertGreater(len(sheet.findall("s:sheetData/s:row/s:c[@r='F13']/s:is/s:r/s:rPr/s:u", NS)), 2)
            self.assertTrue(all(node.get("{http://www.w3.org/XML/1998/namespace}space") == "preserve"
                                for node in sheet.findall("s:sheetData/s:row/s:c[@r='F13']/s:is/s:r/s:t", NS)))
            with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(io.BytesIO(payload)) as output:
                for name in ("xl/styles.xml", "xl/sharedStrings.xml", "xl/theme/theme1.xml"):
                    self.assertEqual(source.read(name), output.read(name))
                before = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
                for path in ("s:cols", "s:mergeCells", "s:pageMargins"):
                    self.assertEqual(ET.tostring(before.find(path, NS)), ET.tostring(sheet.find(path, NS)))
                drawing = ET.fromstring(output.read("xl/drawings/drawing1.xml"))
                self.assertEqual([n.text for n in drawing.findall(".//m:sSubSup//m:t", NS)], ["1800", "0", "+", "25"])
        self.assertEqual(steel_report.TEMPLATE.read_bytes(), original)

    def test_draft_previews_entered_quantities_while_verdict_and_signature_stay_blank(self):
        for mode in ("full", "sampling"):
            record = final_record(failed_data())
            record["status"] = "draft"
            record["data"].update(inspection_mode=mode, supply_quantity=17, inspected_quantity=5,
                                  qualified_quantity=4, rejected_quantity=1, sampling_basis="当班抽检方案")
            _, cells = sheet_cells(steel_report.build_xlsx(record))
            self.assertEqual((cells["B13"], cells["B15"]), ("", ""))
            for detail in ("交检 17 件", "合格 4 件", "不合格 1 件"):
                self.assertIn(detail, cells["F13"])
            if mode == "sampling":
                self.assertIn("当班抽检方案", cells["F13"])
                self.assertIn("抽检 5 件", cells["F13"])
                self.assertIn("抽检结果____，判定本批量____", cells["F13"])
            else:
                self.assertIn("100%检验", cells["F13"])
                self.assertIn("实际检验 5 件", cells["F13"])
                self.assertNotIn("当班抽检方案", cells["F13"])
            preview = steel_report.html_preview(record)
            self.assertIn(cells["F13"], preview)
            self.assertIn("核对并生成后才能下载正式 Excel", preview)

    def test_preview_deferred_values_exclude_legacy_readings_and_count_each_field_once(self):
        record = final_record(steel.employee_example())
        record["status"] = "draft"
        data = record["data"]
        data["values"]["width"].update(source="asr", confirmed=False, value=[1887])
        data["voice_pending"] = ["manufacturer", "width"]
        data["voice_candidates"] = [{"key": "manufacturer", "value": "候选厂名"}]
        preview = steel_report.html_preview(record)
        self.assertIn("2 项语音待核对，候选值尚未写入表格", preview)
        self.assertIn(data["manufacturer"], preview)
        self.assertNotIn("候选厂名", preview)
        self.assertNotIn("1887", preview)

    def test_preview_return_link_reaches_the_same_record_with_or_without_a_mount(self):
        from html.parser import HTMLParser

        class ReturnLink(HTMLParser):
            href = None

            def handle_starttag(self, tag, attrs):
                attrs = dict(attrs)
                if tag == "a" and attrs.get("id") == "return-record":
                    self.href = attrs["href"]

        record = final_record(steel.employee_example())
        record["id"] = 33
        for status in ("draft", "final"):
            record["status"] = status
            parser = ReturnLink()
            parser.feed(steel_report.html_preview(record))
            self.assertTrue(parser.href)
            for mount in ("/", "/s/inspection/"):
                root = "https://test.invalid" + mount
                self.assertEqual(urljoin(root + "api/steel/records/33/preview?v=2", parser.href),
                                 root + "#/steel/33")

    def test_followup_clears_results_and_voice_without_replacing_lot_requirements(self):
        data = failed_data()
        data.update(disposition="拒收", additional_failure_confirmed=True,
                    voice_pending=["surface"], voice_candidates=[{"key": "surface", "value": "不合格"}])
        original = copy.deepcopy(data)
        child = steel.followup_data(data)
        for key in ("document_no", "manufacturer", "product_name", "grade", "specification", "supply_quantity",
                    "inspection_mode", "sampling_basis", "thickness_nominal", "thickness_lower", "thickness_upper",
                    "width_nominal", "width_lower", "width_upper"):
            self.assertEqual(child[key], data[key])
        self.assertEqual(child["inspection_date"], steel.blank_data()["inspection_date"])
        for key in ("values", "evidence", "voice_pending", "voice_candidates", "disposition", "additional_failure_confirmed"):
            self.assertFalse(child[key])
        for key in ("inspected_quantity", "qualified_quantity", "rejected_quantity"):
            self.assertIsNone(child[key])
        self.assertEqual(child["extra_rows"], [{"name": "长度", "requirement": "6000±5", "result": "", "verdict": ""}])
        self.assertFalse(steel.evaluate(child)["can_finalize"])
        self.assertEqual(data, original)


class WorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "isolated.sqlite"
        with sqlite3.connect(str(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            connection.executescript(db.SCHEMA)
            workflow.init_schema(connection)
            steel_standards.init_schema(connection)
            steel_archive.ensure_schema(connection)
            test_accounts.install(connection)
        self.app = Flask(__name__)
        self.app.config["TESTING"] = True
        self.app.secret_key = "isolated-followup"

        def connection():
            if "test_db" not in g:
                g.test_db = sqlite3.connect(str(self.path), timeout=10)
                g.test_db.row_factory = sqlite3.Row
            return g.test_db

        @self.app.teardown_appcontext
        def close_connection(_error):
            current = g.pop("test_db", None)
            if current is not None:
                current.close()

        def need_login(view):
            @wraps(view)
            def secured(*args, **kwargs):
                return view(*args, **kwargs) if identity.current_user(connection()) else (jsonify(error="请登录"), 401)
            return secured

        identity.register(self.app, connection)
        steel_routes.register(self.app, connection, lambda: identity.current_user(connection()), need_login, lambda *_a, **_k: None)
        self.client = self.app.test_client()
        test_accounts.login(self.client)
        engineer = self.app.test_client()
        test_accounts.login(engineer,test_accounts.PROCESS)
        criteria = steel_standards.extract(failed_data())
        result = engineer.post("/api/steel/standards",json=dict(criteria, code="FOLLOWUP-LENGTH", source="复检测试标准"))
        self.assertEqual(result.status_code,201,result.get_json())

    def new(self, data=None):
        response = self.client.post("/api/steel/records", json={"data": data or failed_data()})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["record"]

    def finalize(self, record, client=None):
        return (client or self.client).post("/api/steel/records/%s/finalize" % record["id"],
                                           json={"version": record["version"], "data": record["data"]})

    def rows(self, sql, args=()):
        with sqlite3.connect(str(self.path)) as connection:
            connection.row_factory = sqlite3.Row
            return [dict(row) for row in connection.execute(sql, args)]

    def test_failed_finalize_exports_and_creates_one_empty_linked_draft(self):
        record = self.new()
        response = self.finalize(record)
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        parent, child = result["record"], result["followup"]
        self.assertEqual(parent["evaluation"]["overall"], "fail")
        self.assertEqual((parent["followup_record_id"], result["followup_record_id"]), (child["id"], child["id"]))
        self.assertEqual((parent["followup_status"], child["status"], child["parent_id"]), ("draft", "draft", parent["id"]))
        self.assertEqual(child["data"], steel.followup_data(parent["data"]))
        self.assertFalse(child["evaluation"]["can_finalize"])
        self.assertEqual(sheet_cells(self.client.get("/api/steel/records/%s/xlsx" % parent["id"]).data)[1]["B13"], "不合格")
        before = self.rows("SELECT * FROM steel_records WHERE id=?", (parent["id"],))[0]
        for _ in range(2):
            revised = self.client.post("/api/steel/records/%s/revise" % parent["id"])
            self.assertEqual(revised.status_code, 200)
            self.assertEqual(revised.get_json()["record"]["id"], child["id"])
        self.assertEqual(self.finalize(record).status_code, 403)
        self.assertEqual(len(self.rows("SELECT id FROM steel_records WHERE parent_id=?", (parent["id"],))), 1)
        self.assertEqual(self.client.put("/api/steel/records/%s" % parent["id"], json={"version": parent["version"]}).status_code, 403)
        self.assertEqual(self.finalize(child).status_code, 400)  # Counts and all results must be re-entered.
        saved = self.client.put("/api/steel/records/%s" % child["id"], json={"version": child["version"], "data": child["data"]})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(self.rows("SELECT * FROM steel_records WHERE id=?", (parent["id"],))[0], before)
        listed = self.client.get("/api/steel/records").get_json()["records"]
        listed_parent = next(row for row in listed if row["id"] == parent["id"])
        self.assertEqual(listed_parent["followup_record_id"], child["id"])
        self.assertEqual(self.client.get("/api/steel/records/%s" % parent["id"]).get_json()["record"]["followup_status"], "draft")
        actions = [row["action"] for row in self.rows("SELECT * FROM audit")]
        self.assertIn("steel_followup", actions)
        self.assertIn("steel_finalize", actions)

    def test_pass_has_no_automatic_child_and_invalid_failure_counts_stay_blocked(self):
        passed = self.finalize(self.new(steel.employee_example()))
        self.assertEqual(passed.status_code, 200)
        self.assertIsNone(passed.get_json()["followup"])
        self.assertIsNone(passed.get_json()["record"]["followup_record_id"])
        invalid = failed_data()
        invalid.update(qualified_quantity=1, rejected_quantity=0)
        record = self.new(invalid)
        self.assertEqual(self.finalize(record).status_code, 400)
        self.assertFalse(self.rows("SELECT id FROM steel_records WHERE parent_id=?", (record["id"],)))

    def test_rejected_generation_saves_current_draft_for_preview_and_corrected_export(self):
        record = self.new(steel.blank_data())
        path = "/api/steel/records/%d" % record["id"]
        submitted = steel.normalize(steel.employee_example())
        submitted.update(document_no="DRAFT-046", manufacturer="本次入库单位", is_example=False)
        submitted["values"]["width"]["value"] = [1807]
        submitted["voice_candidates"] = [{"key": "manufacturer", "value": "待核对生产单位", "raw": "待核对生产单位"}]
        rejected = self.client.post(path + "/finalize", json={"version": 1, "data": submitted})
        self.assertEqual(rejected.status_code, 400)
        body = rejected.get_json()
        self.assertTrue(body["draft_saved"])
        draft = self.client.get(path).get_json()["record"]
        self.assertEqual(body["record"], draft)
        self.assertEqual((draft["status"], draft["version"], draft["signed_at"]), ("draft", 2, None))
        self.assertFalse(draft["inspector_name"])
        self.assertFalse(draft["followup_record_id"])
        self.assertEqual(draft["evaluation"], body["evaluation"])
        self.assertEqual(draft["data"]["voice_pending"], ["manufacturer"])
        preview = self.client.get(path + "/preview").get_data(as_text=True)
        for value in ("DRAFT-046", "本次入库单位", "1807"):
            self.assertIn(value, preview)
        self.assertNotIn("待核对生产单位", preview)
        self.assertEqual(self.client.get(path + "/xlsx").status_code, 409)

        repeated = self.finalize(draft)
        self.assertEqual(repeated.status_code, 400)
        self.assertEqual(repeated.get_json()["record"], draft)
        submitted["voice_candidates"] = []
        stale = self.client.post(path + "/finalize", json={"version": 1, "data": submitted})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(path).get_json()["record"], draft)
        accepted = self.client.post(path + "/finalize", json={"version": draft["version"], "data": submitted})
        self.assertEqual(accepted.status_code, 200)
        sheet, cells = sheet_cells(self.client.get(path + "/xlsx").data)
        self.assertEqual((cells["B3"], cells["B4"], cells["B13"]), ("DRAFT-046", "本次入库单位", "合格"))
        self.assertEqual(sheet.findtext(".//s:c[@r='G10']/s:v", namespaces=NS), "1807")

    def test_missing_or_invalid_final_payload_cannot_sign_or_replace_existing_values(self):
        record = self.new(steel.employee_example())  # This saved draft already passes every check.
        path = "/api/steel/records/%d" % record["id"]
        invalid = dict(record["data"], width_nominal="无有效数值")
        partial = {"document_no": "wrong"}
        for body in ([], {"version": 1}, {"version": 1, "data": None},
                     {"version": 1, "data": partial}, {"version": 1, "data": invalid}):
            with self.subTest(body=body):
                self.assertEqual(self.client.put(path, json=body).status_code, 400)
                response = self.client.post(path + "/finalize", json=body)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn("draft_saved", response.get_json())
                self.assertEqual(self.client.get(path).get_json()["record"], record)
        self.assertEqual(self.client.get(path + "/xlsx").status_code, 409)

    def test_child_creation_and_audit_failure_roll_back_the_parent_transition(self):
        record = self.new()
        original_insert = db.insert

        def fail_after_child(connection, table, data):
            if table == "audit" and data.get("action") == "steel_followup":
                raise RuntimeError("simulated follow-up audit failure")
            return original_insert(connection, table, data)

        with patch.object(db, "insert", side_effect=fail_after_child):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                self.finalize(record)
        parent = self.client.get("/api/steel/records/%s" % record["id"]).get_json()["record"]
        self.assertEqual((parent["status"], parent["version"]), ("draft", 1))
        self.assertFalse(self.rows("SELECT id FROM steel_records WHERE parent_id=?", (record["id"],)))
        events = self.rows("SELECT action FROM audit WHERE target=?", ("steel:%d" % record["id"],))
        self.assertEqual([event["action"] for event in events], ["steel_create"])
        self.assertEqual(self.finalize(record).status_code, 200)

    def test_concurrent_finalize_cas_creates_exactly_one_followup(self):
        record = self.new()
        barrier = threading.Barrier(2)
        normalized = threading.local()
        original_normalize = steel.normalize

        def synchronize_first_normalization(data):
            if not getattr(normalized, "already", False):
                normalized.already = True
                barrier.wait(timeout=8)
            return original_normalize(data)

        def finalize_request():
            with self.app.test_client() as client:
                test_accounts.login(client)
                return self.finalize(record, client).status_code

        with patch.object(steel, "normalize", side_effect=synchronize_first_normalization):
            with ThreadPoolExecutor(max_workers=2) as pool:
                statuses = list(pool.map(lambda _index: finalize_request(), range(2)))
        self.assertEqual(sorted(statuses), [200, 409])
        self.assertEqual(len(self.rows("SELECT * FROM steel_records WHERE parent_id=?", (record["id"],))), 1)
        self.assertEqual(len(self.rows("SELECT * FROM audit WHERE action='steel_finalize'")), 1)

    def test_legacy_failed_revise_creates_then_reuses_child_and_exposes_final_status(self):
        record = self.new()
        with sqlite3.connect(str(self.path)) as connection:
            connection.execute("UPDATE steel_records SET status='final' WHERE id=?", (record["id"],))
        response = self.client.post("/api/steel/records/%s/revise" % record["id"])
        self.assertEqual(response.status_code, 201)
        child = response.get_json()["record"]
        self.assertEqual(self.client.post("/api/steel/records/%s/revise" % record["id"]).get_json()["record"]["id"], child["id"])
        measured = steel.employee_example()
        measured["extra_rows"] = [dict(child["data"]["extra_rows"][0],result="6001",verdict="合格")]
        completed = self.client.post("/api/steel/records/%s/finalize" % child["id"],
                                     json={"version": child["version"], "data": measured})
        self.assertEqual(completed.status_code, 200)
        parent = self.client.get("/api/steel/records/%s" % record["id"]).get_json()["record"]
        self.assertEqual(parent["followup_status"], "final")
        self.assertEqual(self.client.post("/api/steel/records/%s/revise" % record["id"]).get_json()["record"]["id"], child["id"])
        self.client.post('/api/logout',json={})
        self.assertEqual(self.client.post("/api/steel/records/%s/revise" % record["id"]).status_code, 401)


if __name__ == "__main__":
    unittest.main(verbosity=2)
