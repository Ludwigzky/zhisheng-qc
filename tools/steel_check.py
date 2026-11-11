# -*- coding: utf-8 -*-
"""Steel-stage regression and one employee-format demonstration XLSX.

All API tests use a temporary database. The output is explicitly an example,
uses the supplied specimen's values, and never copies its signature image.
"""
import copy
import io
import json
import logging
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app as server
import db
import seed
import audio
import asr_engine
import numpy as np
import steel
import steel_download
import steel_report
import test_accounts

NS = steel_report.NS
OUTPUT = ROOT / "_probe/steel-example.xlsx"


def record(data=None):
    return {"data": steel.normalize(data or steel.employee_example()), "status": "final", "inspector_name": "演示检验员",
            "template_hash": steel_report.template_hash(), "snapshot": "example"}


def values(payload):
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        shared = ["".join(n.itertext()) for n in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
        result = {}
        for c in sheet.findall("s:sheetData/s:row/s:c", NS):
            if c.get("t") == "inlineStr":
                value = "".join(c.find("s:is", NS).itertext())
            else:
                v = c.find("s:v", NS)
                value = v.text if v is not None else ""
                if c.get("t") == "s" and value:
                    value = shared[int(value)]
            result[c.get("r")] = value
        return result


class DomainChecks(unittest.TestCase):
    def test_new_inspections_default_to_sampling_without_changing_saved_choices(self):
        self.assertEqual(steel.blank_data()["inspection_mode"], "sampling")
        self.assertEqual(steel.normalize({})["inspection_mode"], "sampling")
        self.assertEqual(steel.normalize({"inspection_mode": "full"})["inspection_mode"], "full")
        self.assertEqual(steel.normalize({"inspection_mode": "sampling"})["inspection_mode"], "sampling")
        self.assertEqual(steel.employee_example()["inspection_mode"], "full")
        data = steel.employee_example()
        data.pop("inspection_mode")
        normalized = steel.normalize(data)
        self.assertEqual(normalized["inspection_mode"], "sampling")
        self.assertEqual(normalized["supply_quantity"], 1)
        self.assertTrue(steel.evaluate(normalized)["can_finalize"])
        self.assertEqual(normalized["inspection_mode"], "sampling")  # Validation never silently changes the method.

    def test_employee_example_and_exact_limits(self):
        data = steel.normalize(steel.employee_example())
        e = steel.evaluate(data)
        self.assertTrue(e["can_finalize"])
        self.assertEqual(e["thickness_range"], [5.46, 6.04])
        for key, valid, invalid in (("thickness", [5.46, 6.04], 6.0401), ("width", [1800, 1825], 1825.01)):
            data["values"][key]["value"] = valid
            self.assertEqual(next(r for r in steel.evaluate(data)["rows"] if r["key"] == key)["status"], "pass")
            data["values"][key]["value"] = [invalid]
            self.assertEqual(next(r for r in steel.evaluate(data)["rows"] if r["key"] == key)["status"], "fail")
            data = steel.normalize(steel.employee_example())

    def test_asr_confirmation_is_required_even_for_plausible_values(self):
        data = steel.employee_example()
        data["values"]["thickness"].update(source="asr", confidence=0, confirmed=False)
        self.assertFalse(steel.evaluate(steel.normalize(data))["can_finalize"])
        data["values"]["thickness"]["confirmed"] = True
        self.assertTrue(steel.evaluate(steel.normalize(data))["can_finalize"])

    def test_counts_cannot_hide_a_failed_item(self):
        data = steel.employee_example()
        data["values"]["surface"]["value"] = "不合格"
        self.assertFalse(steel.evaluate(data)["can_finalize"])
        data.update(qualified_quantity=0, rejected_quantity=1)
        self.assertEqual(steel.evaluate(data)["overall"], "fail")
        self.assertTrue(steel.evaluate(data)["can_finalize"])

    def test_sampling_boundaries_and_disposition(self):
        self.assertEqual([steel.recommended_sample(n) for n in [1, 5, 50, 51, 400]], [1, 5, 5, 5, 30])
        data = steel.employee_example()
        data.update(inspection_mode="sampling", supply_quantity=10, inspected_quantity=5, qualified_quantity=4, rejected_quantity=1)
        self.assertFalse(steel.evaluate(data)["can_finalize"])
        data["disposition"] = "加倍抽检"
        self.assertTrue(steel.evaluate(data)["can_finalize"])
        self.assertEqual(steel.evaluate(data)["overall"], "fail")
        data['disposition'] = '拒收'
        self.assertFalse(steel.evaluate(data)['can_finalize'])
        data.update(additional_failure_confirmed=True, inspected_quantity=10, qualified_quantity=8, rejected_quantity=2)
        self.assertTrue(steel.evaluate(data)['can_finalize'])

    def test_small_lot_sampling_uses_actual_sample_but_keeps_other_count_rules(self):
        data = steel.employee_example()
        for supplied, inspected, mode, expected in (
                (1, 1, "sampling", True), (2, 1, "sampling", True),
                (4, 1, "sampling", True), (4, 0, "sampling", False),
                (4, 5, "sampling", False), (4, 1, "full", False),
                (4, 4, "full", True), (5, 1, "sampling", False),
                (5, 5, "sampling", True), (400, 29, "sampling", False)):
            with self.subTest(supplied=supplied, inspected=inspected, mode=mode):
                data.update(supply_quantity=supplied, inspected_quantity=inspected,
                            qualified_quantity=inspected, rejected_quantity=0, inspection_mode=mode)
                self.assertEqual(steel.evaluate(data)["can_finalize"], expected)

    def test_specification_changes_do_not_reuse_old_requirements_silently(self):
        data = steel.employee_example()
        data["specification"] = "6*2000"
        self.assertFalse(steel.evaluate(data)["can_finalize"])
        data["values"]["width"]["value"] = float("nan")
        with self.assertRaises(ValueError):
            steel.normalize(data)

    def test_not_applicable_only_for_non_coiled_material(self):
        data = steel.employee_example()
        data["values"]["coil"]["value"] = "不适用"
        self.assertFalse(steel.evaluate(data)["can_finalize"])
        data["product_name"] = "钢板"
        self.assertTrue(steel.evaluate(data)["can_finalize"])

    def test_optional_rows_need_a_human_verdict(self):
        data = steel.employee_example()
        data["extra_rows"] = [{"name": "长度", "requirement": "按委托单", "result": "6001"}]
        self.assertFalse(steel.evaluate(data)["can_finalize"])
        data["extra_rows"][0]["verdict"] = "合格"
        self.assertTrue(steel.evaluate(data)["can_finalize"])

    def test_export_preserves_native_math_layout_and_removes_old_signature(self):
        payload = steel_report.build_xlsx(record())
        with zipfile.ZipFile(steel_report.TEMPLATE) as source, zipfile.ZipFile(io.BytesIO(payload)) as output:
            for name in ("xl/styles.xml", "xl/theme/theme1.xml", "xl/sharedStrings.xml"):
                self.assertEqual(source.read(name), output.read(name))
            before, after = [ET.fromstring(z.read("xl/worksheets/sheet1.xml")) for z in (source, output)]
            self.assertEqual(len(after.findall('s:sheetPr', NS)), 1)
            self.assertGreater(len(after.findall("s:sheetData/s:row/s:c[@r='F13']/s:is/s:r/s:rPr/s:u", NS)), 2)
            for path in ("s:cols", "s:mergeCells", "s:pageMargins"):
                self.assertEqual(ET.tostring(before.find(path, NS)), ET.tostring(after.find(path, NS)))
            self.assertEqual([n.attrib for n in before.findall("s:sheetData/s:row", NS)], [n.attrib for n in after.findall("s:sheetData/s:row", NS)])
            drawing = ET.fromstring(output.read("xl/drawings/drawing1.xml"))
            self.assertEqual(len(drawing.findall(".//m:sSubSup", NS)), 1)
            self.assertEqual([n.text for n in drawing.findall(".//m:sSubSup//m:t", NS)], ["1800", "0", "+", "25"])
            self.assertNotIn("xl/media/image1.jpeg", output.namelist())
            self.assertNotIn("电子签名", output.read("xl/drawings/drawing1.xml").decode())
        cells = values(payload)
        self.assertEqual((cells["B3"], cells["G9"], cells["G10"]), ("2603389", "5.64", "1811"))
        self.assertEqual(cells["B15"], "演示检验员")
        self.assertEqual(cells["B13"], "合格")
        self.assertIn("100%检验", cells["F13"])
        # This fixture represents a historical final without a signed sampling-note snapshot.
        self.assertEqual(cells["B14"], values(steel_report.TEMPLATE.read_bytes())["B14"])

    def test_width_math_updates_and_text_stays_literal(self):
        r = record()
        r["data"].update(width_nominal=2000, width_lower=-2, width_upper=30,
                          specification="5.75*2000", document_no="=SUM(1,1)")
        r["data"]["values"]["width"]["value"] = [2001]
        payload = steel_report.build_xlsx(r)
        with zipfile.ZipFile(io.BytesIO(payload)) as output:
            drawing = ET.fromstring(output.read("xl/drawings/drawing1.xml"))
            self.assertEqual([n.text for n in drawing.findall(".//m:sSubSup//m:t", NS)], ["2000", "-2", "+", "30"])
            sheet = ET.fromstring(output.read("xl/worksheets/sheet1.xml"))
            self.assertEqual(sheet.find("s:sheetData/s:row/s:c[@r='B3']", NS).get("t"), "inlineStr")
        self.assertEqual(values(payload)["B3"], "=SUM(1,1)")


class ApiChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for name, val in {"DATA_DIR": str(root), "DB_PATH": str(root / "test.db"), "AUDIO_DIR": str(root / "audio"), "REPORT_DIR": str(root / "reports")}.items():
            p = patch.object(db, name, val); p.start(); self.addCleanup(p.stop)
        db.init_db(); c = db.connect(); seed.seed_if_empty(c); test_accounts.install(c); c.close()
        self.client = server.app.test_client()
        test_accounts.login(self.client)

    def new(self, data=None):
        response = self.client.post("/api/steel/records", json={"data": data or steel.employee_example()})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["record"]

    def test_create_finalize_export_and_append_reinspection(self):
        r = self.new()
        self.assertEqual(self.client.get('/api/steel/records/%d/xlsx' % r['id']).status_code, 409)
        response = self.client.post('/api/steel/records/%d/finalize' % r['id'], json={"version": r["version"], "data": r["data"], "inspector_name": "伪造名字"})
        self.assertEqual(response.status_code, 200, response.get_json())
        final = response.get_json()["record"]
        self.assertEqual(final["inspector_name"], "演示检验员")
        exported = self.client.get('/api/steel/records/%d/xlsx' % r['id'])
        self.assertEqual(exported.status_code, 200)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(exported.data)
        self.assertEqual(values(exported.data)["B15"], "演示检验员")
        self.assertEqual(self.client.put('/api/steel/records/%d' % r['id'], json={"version": final["version"]}).status_code, 403)
        child = self.client.post('/api/steel/records/%d/revise' % r['id']).get_json()["record"]
        self.assertEqual(child["parent_id"], r["id"])
        self.assertEqual(child["status"], "draft")

    def test_one_coil_sampling_failure_requires_disposition_and_exports_failed_result(self):
        data = steel.employee_example()
        data.update(document_no="ONE-COIL-FAIL", is_example=False, inspection_mode="sampling",
                    qualified_quantity=0, rejected_quantity=1)
        data["values"]["surface"]["value"] = "不合格"
        record = self.new(data)
        path = "/api/steel/records/%d" % record["id"]
        for disposition in ("", "拒收"):
            record["data"]["disposition"] = disposition
            blocked = self.client.post(path + "/finalize", json={"version": record["version"], "data": record["data"]})
            self.assertEqual(blocked.status_code, 400, blocked.get_json())
            self.assertIn("disposition", {issue["key"] for issue in blocked.get_json()["evaluation"]["issues"]})
            record = blocked.get_json()["record"]
        record["data"]["additional_failure_confirmed"] = True
        signed = self.client.post(path + "/finalize", json={"version": record["version"], "data": record["data"]})
        self.assertEqual(signed.status_code, 200, signed.get_json())
        self.assertEqual(signed.get_json()["followup"]["status"], "draft")
        cells = values(self.client.get(path + "/xlsx").data)
        self.assertEqual(cells["B13"], "不合格")
        self.assertIn("抽检1件", "".join(cells["F13"].split()))
        self.assertIn("拒收", cells["F13"])

    def test_download_manager_without_cookies_probes_and_resumes_same_workbook(self):
        data = steel.employee_example()
        data["document_no"] = "DL-111"
        record = self.new(data)
        rid = record["id"]
        path = "/api/steel/records/%d" % rid
        guest = server.app.test_client()
        self.assertEqual(guest.post(path + "/download-link").status_code, 401)
        self.assertEqual(self.client.post(path + "/download-link").status_code, 409)
        self.assertEqual(guest.get(path + "/xlsx").status_code, 401)
        self.assertEqual(self.client.post(path + "/finalize", json={
            "version": record["version"], "data": record["data"]}).status_code, 200)
        link = self.client.post(path + "/download-link")
        self.assertEqual(link.status_code, 200)
        self.assertIn("no-store", link.headers["Cache-Control"])
        url = link.get_json()["url"]
        head = guest.head(url)
        self.assertEqual(head.status_code, 200)
        self.assertEqual(head.data, b"")
        self.assertEqual(head.headers["Accept-Ranges"], "bytes")
        size = int(head.headers["Content-Length"])
        first = guest.get(url, headers={"Range": "bytes=0-55"})
        self.assertEqual((first.status_code, first.headers["Content-Range"]), (206, "bytes 0-55/%d" % size))
        rest = guest.get(url, headers={"Range": "bytes=56-", "If-Range": head.headers["ETag"]})
        self.assertEqual(rest.status_code, 206)
        self.assertEqual(guest.get(url, headers={"Range": "bytes=%d-" % size}).status_code, 416)
        c = db.connect()
        self.assertEqual(c.execute("SELECT count(*) FROM audit WHERE action='steel_export'").fetchone()[0], 0)
        c.close()
        full = guest.get(url)
        self.assertEqual(len(full.data), size)
        self.assertEqual(full.data, first.data + rest.data)
        self.assertEqual(values(full.data)["B3"], "DL-111")
        self.assertIn("filename*=UTF-8''", full.headers["Content-Disposition"])
        self.assertEqual(full.data, self.client.get(path + "/xlsx").data)

        unsigned = url.partition("?")[0]
        self.assertEqual(guest.get(unsigned).status_code, 403)
        self.assertEqual(guest.get(url.replace("ticket=", "ticket=invalid")).status_code, 403)
        other = url.replace("/%d/download/steel-%d.xlsx" % (rid, rid), "/999/download/steel-999.xlsx")
        self.assertEqual(guest.get(other).status_code, 403)
        with patch("itsdangerous.timed.TimestampSigner.get_timestamp",
                   return_value=int(time.time()) + steel_download.TICKET_SECONDS + 2):
            self.assertEqual(guest.get(url).status_code, 403)
        c = db.connect()
        c.execute("UPDATE steel_records SET version=version+1 WHERE id=?", (rid,))
        c.commit()
        c.close()
        self.assertEqual(guest.get(url).status_code, 403)

    def test_mobile_grants_stay_out_of_werkzeug_log_and_filenames_are_portable(self):
        log = logging.LogRecord("werkzeug", logging.INFO, "", 0,
                                '"%s" %s', ("GET /download/steel-1.xlsx?ticket=abc.A_B-12.34 HTTP/1.1", 206), None)
        self.assertTrue(steel_download.RedactDownloadTicket().filter(log))
        self.assertEqual(log.getMessage(), '"GET /download/steel-1.xlsx?ticket=[redacted] HTTP/1.1" 206')
        r = record()
        r["id"] = 1
        r["data"]["document_no"] = "../含斜杠/与引号\"\r\n编号"
        name = steel_download.workbook_filename(r)
        self.assertTrue(name.endswith(".xlsx"))
        self.assertFalse(set('/"\r\n') & set(name))

    def test_missing_pending_conflicts_and_permissions(self):
        data = steel.employee_example()
        data["values"]["width"].update(source="asr", confidence=0, confirmed=False)
        r = self.new(data)
        self.assertEqual(self.client.post('/api/steel/records/%d/finalize' % r['id'], json={"version":1}).status_code, 400)
        self.assertEqual(self.client.put('/api/steel/records/%d' % r['id'], json={"version":0}).status_code, 409)
        test_accounts.login(self.client, test_accounts.SUPERVISOR)
        self.assertEqual(self.client.put('/api/steel/records/%d' % r['id'], json={"version":1}).status_code, 403)
        anonymous = server.app.test_client()
        self.assertEqual(anonymous.get('/api/steel/records').status_code, 401)

    def test_parse_and_legacy_reset_do_not_bypass_stage_controls(self):
        r = self.new()
        parsed = self.client.post('/api/steel/asr/parse', json={"text":"厚度五点六四，宽度一千八百一十一", "current_key":"thickness", "asr_confidence":0}).get_json()
        self.assertEqual({a["key"]:a["value"] for a in parsed["assignments"]}, {"thickness":5.64, "width":1811})
        self.assertTrue(all(a["confidence"] == 0 for a in parsed["assignments"]))
        self.assertIn(self.client.post('/api/demo/reset').status_code, (403, 404, 405, 409))
        self.assertIsNotNone(self.client.get('/api/steel/records/%d' % r['id']).get_json()["record"])

    def test_audio_upload_uses_steel_fields_and_preserves_zero_score(self):
        from types import SimpleNamespace
        r = self.new()
        wave = Path(self.tmp.name) / 'input.wav'
        audio.write_wav(str(wave), 16000, .2 * np.sin(np.arange(16000) * .06))
        engine = SimpleNamespace(name='test', available=True,
            info=lambda:{'engine':'test','available':True,'mode':'dictation','supports_grammar':False},
            set_dictation=lambda:{'ok':True,'mode':'dictation'}, set_context=lambda *a:None,
            transcribe=lambda p:{'text':'厚度五点六四，宽度一千八百一十一','confidence':0,'elapsed_ms':1,'error':None})
        with patch.object(asr_engine, '_ENGINE', engine):
            response = self.client.post('/api/steel/asr/audio', data={'record_id':str(r['id']), 'current_key':'thickness',
                'asr_mode':'dictation', 'audio':(io.BytesIO(wave.read_bytes()), 'input.wav')})
        self.assertEqual(response.status_code, 200, response.get_json())
        result = response.get_json()
        self.assertEqual(result['record_id'], r['id'])
        self.assertEqual({a['key']:a['value'] for a in result['assignments']}, {'thickness':5.64,'width':1811})
        self.assertTrue(all(a['confidence'] == 0 for a in result['assignments']))
        self.assertTrue((Path(db.AUDIO_DIR) / result['audio_id']).is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
