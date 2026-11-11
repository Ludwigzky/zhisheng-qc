# -*- coding: utf-8 -*-
"""Typed voice regression. Fake decoder + temporary SQLite, never user data."""
import copy
import io
from pathlib import Path
import json
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app as server
import test_accounts
import asr_engine
import audio
import db
import numpy as np
import seed
import steel
import steel_standards
import steel_voice as voice

BATCH_MANUAL_ONLY = {"inspection_mode", "inspected_quantity", "qualified_quantity", "rejected_quantity",
                     "sampling_basis", "disposition", "additional_failure_confirmed"}
CHECK_MANUAL_ONLY = {key for key, _label, _requirement in steel.CHECKS}
MANUAL_ONLY = BATCH_MANUAL_ONLY | CHECK_MANUAL_ONLY


class TypedVoiceChecks(unittest.TestCase):
    def parsed(self, key, text, score=None):
        return voice.parse_utterance(text, key, score)

    def value(self, key, text):
        parsed = self.parsed(key, text)
        self.assertEqual(len(parsed["assignments"]), 1, parsed)
        candidate = parsed["assignments"][0]
        self.assertEqual(candidate["key"], key)
        self.assertFalse(candidate["confirmed"])
        self.assertTrue(candidate["requires_confirmation"])
        self.assertEqual(candidate["source"], "asr")
        return candidate["value"]

    def rejected(self, key, text):
        parsed = self.parsed(key, text)
        self.assertEqual(parsed["assignments"], [], parsed)
        self.assertTrue(parsed["unknown"])

    @unittest.skipUnless(shutil.which("node"), "Node unavailable")
    def test_frontend_manual_checks_ignore_legacy_overlay_and_cannot_confirm_old_speech(self):
        script = r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const catalog=JSON.parse(process.argv[2]);
const checks=['cert_provided','cert_performance','identity_match','cert_seal','surface','shape','coil'];
const sandbox={console,JSON,Array,String,Number,Error,Object,
  S:{me:{id:1},recording:false},
  Steel:{template:{voice_targets:catalog,review_targets:catalog},record:{id:1,status:'draft',owner_id:1},
    data:{values:{},voice_candidates:checks.map(key=>({key,value:'不合格',raw:'历史语音',source:'asr'})),voice_pending:checks.slice()}},
  esc:value=>String(value),window:{addEventListener(){}},document:{}};
for(const key of checks)sandbox.Steel.data.values[key]={value:'合格',source:'manual',confirmed:true};
vm.createContext(sandbox);vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),sandbox);
for(const key of checks){
  sandbox.key=key;
  assert.equal(vm.runInContext('steelManualCheck(key)',sandbox),true);
  assert.equal(vm.runInContext('steelTarget(key)',sandbox),undefined);
  assert.equal(vm.runInContext('steelVoiceButton(key)',sandbox),'');
  assert.equal(vm.runInContext('steelDisplayValue(key)',sandbox),'合格','Historical candidate must not replace a manual choice');
  assert.throws(()=>vm.runInContext('steelConfirmCandidate(key)',sandbox),/手选/);
}
assert.equal(catalog.filter(target=>{sandbox.key=target.key;return vm.runInContext('!!steelTarget(key)',sandbox);}).length,8);
sandbox.Steel.data.extra_rows=[{name:'长度'},{name:'平整度'}];
assert.equal(catalog.filter(target=>{sandbox.key=target.key;return vm.runInContext('!!steelTarget(key)',sandbox);}).length,12);
const review=vm.runInContext('steelReview()',sandbox);
assert.equal((review.match(/data-steel-act="voice-discard"/g)||[]).length,7);
assert.ok(!review.includes('data-steel-act="voice-apply"'));
assert.ok(!review.includes('data-steel-act="voice-rerecord"'));
assert.ok(!review.includes('data-steel-candidate='));
sandbox.Steel.data.values.surface={value:'不合格',source:'asr',confirmed:false};
assert.equal(vm.runInContext("steelDisplayValue('surface')",sandbox),'','Unconfirmed historic speech requires an explicit manual choice');
sandbox.Steel.record.status='final';
assert.equal(vm.runInContext("steelDisplayValue('surface')",sandbox),'不合格','Readonly historical values must remain visible');
console.log('manual-only checks and historical review regression OK');
'''
        completed = subprocess.run([shutil.which("node"), "-e", script,
                                    str(ROOT / "frontend" / "steel_voice.js"),
                                    json.dumps(voice.review_targets(), ensure_ascii=False)],
                                   capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)

    def test_catalog_omits_check_and_batch_voice_but_preserves_historical_candidate_types(self):
        historical = set(steel.HEADER_TEXT) | set(steel.NUMBER_KEYS) | steel.FIELD_KEYS | {
            "inspection_mode", "additional_failure_confirmed"}
        historical |= {"extra_%d_%s" % (i, p) for i in range(2) for p in ("name", "requirement", "result", "verdict")}
        historical.discard("heat_no")  # ledger metadata entered manually
        expected = historical - MANUAL_ONLY - steel_standards.LOCKED_VOICE
        self.assertEqual(set(voice.TARGETS), historical)
        self.assertEqual(len(historical), 37)
        self.assertEqual(len(expected), 12)
        self.assertEqual(set(voice.MANUAL_ONLY_KEYS), MANUAL_ONLY)
        self.assertEqual({target["key"] for target in voice.voice_targets()}, expected)
        self.assertEqual({target["key"] for target in voice.review_targets()}, historical)
        fields = voice.schema()["sections"][0]["fields"]
        self.assertEqual({f["key"] for f in fields}, expected)
        for field in fields:
            self.assertFalse(set(field) & {"nominal", "lower", "upper", "requirement", "value", "answer"})
            if voice.TARGETS[field["key"]]["kind"] in ("text", "identifier", "date"):
                self.assertEqual(field["type"], "text")
        public = voice.voice_targets()
        public[0]["label"] = "changed"
        self.assertEqual(voice.TARGETS["document_no"]["label"], "单据号")

    def test_each_target_produces_a_typed_review_candidate(self):
        samples = {"document_no": "零零二六零三三八九", "product_name": "钢板", "specification": "六乘两千",
                   "manufacturer": "河北钢铁集团", "supply_quantity": "五十件", "grade": "Q三五五B",
                   "inspection_date": "二零二六年十一月十日", "inspection_mode": "抽样检验",
                   "inspected_quantity": "五", "qualified_quantity": "四", "rejected_quantity": "一",
                   "sampling_basis": "按本批委托单抽取", "disposition": "加倍抽检",
                   "additional_failure_confirmed": "追加检验仍不合格", "thickness": "六点零一毫米", "width": "两千零一毫米"}
        for key in (target["key"] for target in voice.voice_targets()):
            target = voice.TARGETS[key]
            if key not in samples:
                if target["kind"] == "enum":
                    samples[key] = "合格"
                elif target["kind"] == "number":
                    samples[key] = "零" if key.endswith(("_lower", "_upper")) else "六"
                else:
                    samples[key] = {"name": "长度", "requirement": "按委托要求", "result": "六千零一毫米"}[target["extra_prop"]]
            with self.subTest(key=key):
                self.value(key, target["label"] + "：" + samples[key])

    def test_identifiers_preserve_leading_zero_and_letter_digit_content(self):
        self.assertEqual(self.value("document_no", "单据号 零 零 幺 二 六 零 三"), "0012603")
        self.assertEqual(self.value("document_no", "AB-零零二六/一零"), "AB-0026/10")
        self.assertEqual(self.value("grade", "牌号 q 三 五 五 b"), "Q355B")
        for key, text in (("document_no", "单据号十二点五"), ("document_no", "日期2026年11月10日"),
                          ("grade", "Q三五五B，厂家鞍钢"), ("document_no", "炉批号2603389")):
            self.rejected(key, text)

    def test_dates_are_calendar_valid_not_measurement_numbers(self):
        self.assertEqual(self.value("inspection_date", "检验日期二零二六年十一月十日"), "2026-11-10")
        self.assertEqual(self.value("inspection_date", "2024/2/29"), "2024-02-29")
        for text in ("2026年2月29日", "2026-13-1", "十一月十日", "二六年十一月十日", "2026年11月10日宽度1811", "2603389"):
            self.rejected("inspection_date", text)

    def test_numeric_types_units_signed_tolerances_and_counts(self):
        for key in steel_standards.LOCKED_VOICE:
            with self.subTest(key=key),self.assertRaises(ValueError):
                self.parsed(key,"一")
        self.assertEqual(self.value("width", "二米"), 2000)
        self.assertEqual(self.value("supply_quantity", "十件"), 10)
        for key, text in (("supply_quantity", "五点五件"), ("supply_quantity", "负一"),
                          ("supply_quantity", "五毫米"), ("thickness", "零")):
            self.rejected(key, text)

    def test_colloquial_thousands_apply_to_measurements_but_not_identifiers_or_dates(self):
        digits = "一二三四五六七八九"
        for thousands, first in enumerate(digits, 1):
            for hundreds, last in enumerate(digits, 1):
                text = "宽度" + first + "千" + last
                with self.subTest(text=text):
                    candidate = self.parsed("width", text, .12)["assignments"][0]
                    self.assertEqual(candidate["value"], thousands * 1000 + hundreds * 100)
                    self.assertEqual(candidate["raw"], text)
                    self.assertFalse(candidate["confirmed"])
                    self.assertLessEqual(candidate["confidence"], .12)
        self.assertEqual(self.value("width", "宽度一千零一"), 1001)
        self.assertEqual(self.value("width", "宽度一千一十"), 1010)
        self.assertEqual(self.value("width", "一千一毫米"), 1100)
        self.assertEqual(self.value("document_no", "零零一一零零"), "001100")
        self.assertEqual(self.value("grade", "Q一一零零B"), "Q1100B")
        self.rejected("document_no", "一千一")
        self.assertEqual(self.value("inspection_date", "一千一年一月一日"), "1001-01-01")
        self.assertEqual(self.value("manufacturer", "一千一钢铁集团"), "一千一钢铁集团")

    def test_numeric_specification_operands_use_canonical_stars_and_reject_free_text(self):
        cases = (("一千一乘两千二", "1100*2200"), ("五点七五乘一千一", "5.75*1100"),
                 ("5.75乘1800", "5.75*1800"), ("5.75 乘上 1千8", "5.75*1800"),
                 ("六乘以2000", "6*2000"), ("零点五乘点二五", "0.5*0.25"),
                 ("1.25×二千零二", "1.25*2002"), ("一千零一乘一千一十", "1001*1010"),
                 ("５．７５乘１８００", "5.75*1800"), ("1.5乘2.25乘3", "1.5*2.25*3"))
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(voice._specification(voice._normalized(text)), expected)
        for text in ("xxx乘xxx", "钢板乘1800", "5.75乘未知", "5.75乘牌号Q355B", "5.75乘", "乘1800",
                     "0乘1800", "5.75乘负1800", "1乘2乘3乘4", "1.2.3乘1800", "1e3乘1800"):
            with self.subTest(text=text):
                self.assertIsNone(voice._specification(voice._normalized(text)))

    def test_enumeration_negations_and_conflicts_cannot_become_pass(self):
        self.assertEqual(self.value("extra_0_verdict", "追加项目1判定不合格"), "不合格")
        for key, text in (("extra_0_verdict", "没有不合格"), ("extra_0_verdict", "不是不合格"), ("extra_0_verdict", "未发现不合格"),
                          ("extra_0_verdict", "不确定是否合格"), ("extra_0_verdict", "合格或不合格"),
                          ("extra_0_verdict", "不适用"), ("extra_0_verdict", "追加项目1判定合格，追加项目1判定不合格")):
            self.rejected(key, text)

    def test_verdict_hotword_variants_keep_raw_score_and_negation_context(self):
        for text, expected in (("和格", "合格"), ("何格", "合格"), ("不和格", "不合格"), ("布合格", "不合格")):
            with self.subTest(text=text):
                candidate = self.parsed("extra_0_verdict", text, .12)["assignments"][0]
                self.assertEqual(candidate["value"], expected)
                self.assertEqual(candidate["raw"], text)
                self.assertLessEqual(candidate["confidence"], .12)
                self.assertFalse(candidate["confirmed"])
                self.assertTrue(candidate["requires_confirmation"])
        for text in ("不，合格", "不，和格", "可能，不合格", "未发现，合格", "追加项目1判定不，合格"):
            with self.subTest(text=text):
                self.rejected("extra_0_verdict", text)
        self.assertEqual(self.value("manufacturer", "和格钢铁集团"), "和格钢铁集团")

    def test_manual_check_and_batch_fields_cannot_be_targeted_or_inferred_from_speech(self):
        for key in MANUAL_ONLY:
            target = voice.TARGETS[key]
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parsed(key, "合格")
            inferred = voice.parse_utterance(target["label"] + "合格")
            self.assertFalse(any(item["key"] in MANUAL_ONLY for item in inferred["assignments"]))
        # Existing draft candidates remain reviewable after removing new entry points.
        data = steel.employee_example()
        data["voice_candidates"] = [{"key": "additional_failure_confirmed", "value": True}]
        normalized = steel.normalize(data)
        self.assertEqual(normalized["voice_candidates"][0]["key"], "additional_failure_confirmed")
        self.assertFalse(steel.evaluate(normalized)["can_finalize"])
        for key in CHECK_MANUAL_ONLY:
            legacy = steel.employee_example()
            legacy["voice_candidates"] = [{"key": key, "value": "不合格", "raw": "历史语音"}]
            normalized = steel.normalize(legacy)
            self.assertEqual(normalized["voice_candidates"][0]["key"], key)
            self.assertEqual(normalized["values"], steel.normalize(steel.employee_example())["values"])
            self.assertFalse(steel.evaluate(normalized)["can_finalize"])
        mixed = voice.parse_utterance("厚度五点六四，表面质量合格，质量证明书不合格", "thickness")
        self.assertEqual([candidate["key"] for candidate in mixed["assignments"]], ["thickness"])
        self.assertEqual(len(mixed["unknown"]), 2)

    def test_no_cross_field_filling_or_unlabelled_cursor_advance(self):
        for key, text in (("manufacturer", "产品名称钢卷"), ("product_name", "厂家鞍钢"),
                          ("manufacturer", "厂家鞍钢，牌号Q355B"), ("manufacturer", "陌生字段：张三"),
                          ("manufacturer", "检验员张三"), ("thickness", "单据号2603389"),
                          ("supply_quantity", "检验日期2026年11月10日")):
            self.rejected(key, text)
        for key in ("extra_0_name", "extra_1_requirement"):
            with self.assertRaises(ValueError):
                self.parsed(key, "厚度")
        self.assertEqual(self.value("manufacturer", "为华钢铁集团"), "为华钢铁集团")
        self.assertEqual(self.value("extra_0_result", "暂不能确认，需复验"), "暂不能确认,需复验")
        parsed = self.parsed("thickness", "厚度五点六四宽度一千八百一十一", 0)
        self.assertEqual({a["key"]: a["value"] for a in parsed["assignments"]}, {"thickness": 5.64, "width": 1811})
        self.assertTrue(all(a["confidence"] == 0 for a in parsed["assignments"]))
        self.assertEqual(self.value("thickness", "五点六四，五点六五"), [5.64, 5.65])
        self.assertEqual(voice.parse_utterance("随便一句话")["assignments"], [])
        self.assertEqual(voice.parse_utterance("检验员张三")["assignments"], [])

    def test_invalid_targets_and_scores_are_explicit_errors(self):
        for key in ("inspector_name", "signature", "extra_9_result", ["thickness"], ""):
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.parsed(key, "张三")
        for score in (True, -1, 1.1, float("nan"), float("inf"), "bad"):
            with self.subTest(score=score), self.assertRaises(ValueError):
                self.parsed("thickness", "六", score)

    def test_pending_candidates_are_json_safe_bounded_and_canonical(self):
        base = steel.employee_example()
        candidate = voice.parse_utterance("长度六千零一", "extra_1_result")["assignments"][0]
        candidate.update(destination="header", extra_index=0, extra_prop="manufacturer", label="伪装", confirmed=True)
        base["voice_candidates"] = [candidate, dict(candidate, value="长度六千零二")]
        base["voice_pending"] = ["manufacturer", "manufacturer"]
        normalized = steel.normalize(base)
        self.assertEqual(normalized["voice_pending"], ["manufacturer", "extra_1_result"])
        self.assertEqual(len(normalized["voice_candidates"]), 1)
        item = normalized["voice_candidates"][0]
        self.assertEqual(item["value"], "长度六千零二")
        self.assertEqual((item["destination"], item["extra_index"], item["extra_prop"]), ("extra", 1, "result"))
        self.assertFalse(item["confirmed"])
        self.assertEqual(normalized["extra_rows"], [])
        for invalid in (float("nan"), float("inf"), 10 ** 400, {"payload": 5}, [[5]], [True], [1] * 13):
            payload = steel.employee_example()
            payload["voice_candidates"] = [{"key": "thickness", "value": invalid}]
            with self.subTest(value=invalid), self.assertRaises(ValueError):
                steel.normalize(payload)
        for field, value in (("voice_pending", ["inspector_name"]), ("voice_pending", "thickness"),
                              ("voice_candidates", [{"key": "signature", "value": "某人"}]),
                              ("voice_candidates", [{"key": "manufacturer", "value": "A" * 161}]),
                              ("voice_candidates", [{"key": "manufacturer", "value": "A", "raw": "B" * 2001}]),
                              ("voice_candidates", [{"key": "manufacturer", "value": "A", "confidence": 2}]),
                              ("voice_candidates", [{"key": "surface", "value": "不适用"}]),
                              ("voice_candidates", [{"key": "additional_failure_confirmed", "value": "maybe"}])):
            payload = steel.employee_example()
            payload[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                steel.normalize(payload)


class ApiVoiceChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        for key, value in {"DATA_DIR": str(root), "DB_PATH": str(root / "test.db"),
                           "AUDIO_DIR": str(root / "audio"), "REPORT_DIR": str(root / "reports")}.items():
            setting = patch.object(db, key, value)
            setting.start()
            self.addCleanup(setting.stop)
        db.init_db()
        connection = db.connect()
        seed.seed_if_empty(connection)
        test_accounts.install(connection)
        connection.close()
        self.client = server.app.test_client()
        test_accounts.login(self.client)
        response = self.client.post("/api/steel/records", json={"data": steel.employee_example()})
        self.assertEqual(response.status_code, 201)
        self.record = response.get_json()["record"]

    def test_catalog_and_parse_candidates_do_not_change_the_record(self):
        self.assertEqual(self.client.get("/api/steel/template").get_json()["voice_targets"], voice.voice_targets())
        self.assertEqual(self.client.get("/api/steel/voice-fields").get_json()["voice_targets"], voice.voice_targets())
        response = self.client.post("/api/steel/asr/parse", json={"text": "单据号零零一二三", "current_key": "document_no", "asr_confidence": 0})
        candidate = response.get_json()["assignments"][0]
        self.assertEqual(candidate["value"], "00123")
        self.assertEqual(candidate["destination"], "header")
        self.assertEqual(candidate["confidence"], 0)
        for body in ({"current_key": "inspector_name"}, {"current_key": ["width"]}, {"current_key": "width", "asr_confidence": True}, [1]):
            self.assertEqual(self.client.post("/api/steel/asr/parse", json=body).status_code, 400)
        after = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        self.assertEqual(after, self.record)

    def test_audio_uses_the_same_typed_parser_for_headers_and_extra_rows(self):
        wave = Path(self.tmp.name) / "input.wav"
        audio.write_wav(str(wave), 16000, .2 * np.sin(np.arange(16000) * .06))
        contexts = []
        for key, text, expected in (("document_no", "单据号零零二六零三", "002603"),
                                    ("inspection_date", "二零二六年十一月十日", "2026-11-10"),
                                    ("extra_1_result", "长度六千零一毫米", "长度六千零一毫米"),
                                    ("extra_0_verdict", "追加项目1判定不合格", "不合格"),
                                    ("width", "宽度一千一", 1100)):
            engine = SimpleNamespace(name="test", available=True,
                info=lambda: {"engine": "test", "available": True, "mode": "dictation", "supports_grammar": False},
                set_dictation=lambda: {"ok": True, "mode": "dictation"},
                set_context=lambda schema, current: contexts.append((schema, current)),
                transcribe=lambda p: {"text": text, "confidence": 0, "elapsed_ms": 1, "error": None})
            with self.subTest(key=key), patch.object(asr_engine, "_ENGINE", engine):
                response = self.client.post("/api/steel/asr/audio", data={"record_id": str(self.record["id"]),
                    "current_key": key, "asr_mode": "dictation", "audio": (io.BytesIO(wave.read_bytes()), "input.wav")})
                self.assertEqual(response.status_code, 200, response.get_json())
                parsed = response.get_json()
                self.assertEqual(parsed["assignments"][0]["value"], expected)
                self.assertEqual(parsed["assignments"][0]["confidence"], 0)
                self.assertFalse(parsed["assignments"][0]["confirmed"])
                self.assertTrue(parsed["requires_confirmation"])
                self.assertEqual(contexts[-1][1], key)
                self.assertEqual(contexts[-1][0], voice.schema())
        after = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        self.assertEqual(after, self.record)
        response = self.client.post("/api/steel/asr/audio", data={"record_id": str(self.record["id"]),
            "current_key": "inspector_name", "audio": (io.BytesIO(wave.read_bytes()), "input.wav")})
        self.assertEqual(response.status_code, 400)

    def test_manual_check_and_batch_sections_reject_speech_and_remain_manually_editable(self):
        wave = Path(self.tmp.name) / "blocked-batch.wav"
        audio.write_wav(str(wave), 16000, .2 * np.sin(np.arange(8000) * .06))
        for key in MANUAL_ONLY:
            with self.subTest(key=key):
                response = self.client.post("/api/steel/asr/parse", json={"current_key": key, "text": "五"})
                self.assertEqual(response.status_code, 400, response.get_json())
                response = self.client.post("/api/steel/asr/audio", data={"record_id": str(self.record["id"]),
                    "current_key": key, "audio": (io.BytesIO(wave.read_bytes()), "blocked-batch.wav")})
                self.assertEqual(response.status_code, 400, response.get_json())
        data = copy.deepcopy(self.record["data"])
        data.update(supply_quantity=10, inspection_mode="full", inspected_quantity=10,
                    qualified_quantity=10, rejected_quantity=0, sampling_basis="按本批委托单",
                    disposition="", additional_failure_confirmed=False)
        response = self.client.put("/api/steel/records/%d" % self.record["id"],
                                   json={"version": self.record["version"], "data": data})
        self.assertEqual(response.status_code, 200, response.get_json())
        saved = response.get_json()["record"]
        self.assertTrue(saved["evaluation"]["can_finalize"])
        for key in BATCH_MANUAL_ONLY:
            self.assertEqual(saved["data"][key], data[key])
        for key in CHECK_MANUAL_ONLY:
            self.assertEqual(saved["data"]["values"][key], data["values"][key])
            self.assertEqual(saved["data"]["values"][key]["source"], "manual")

    def test_route_also_blocks_pending_top_level_voice_before_signing(self):
        data = copy.deepcopy(self.record["data"])
        data["voice_pending"] = ["manufacturer"]
        # Independently exercise the route defense while the domain normalizer
        # is tested by steel_check.py. No signature should be created.
        with patch.object(steel, "normalize", return_value=data):
            response = self.client.post("/api/steel/records/%d/finalize" % self.record["id"],
                                       json={"version": self.record["version"], "data": data})
        self.assertEqual(response.status_code, 400)
        after = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        self.assertEqual(after["status"], "draft")
        self.assertFalse(after["inspector_name"])

    def test_saved_review_candidates_survive_refresh_and_cannot_omit_pending(self):
        original = copy.deepcopy(self.record["data"])
        cases = (("document_no", "零零一二三"), ("inspection_date", "2026年11月10日"),
                 ("supply_quantity", "六十"), ("extra_0_verdict", "不合格"),
                 ("extra_0_result", "六千零一毫米"), ("width", "一千八百一十一"))
        candidates = [voice.parse_utterance(text, key)["assignments"][0] for key, text in cases]
        # Existing records keep their old candidate audit trail even though
        # checks can no longer be targeted by a new speech request.
        candidates.append({"key": "surface", "value": "不合格", "raw": "历史语音表面质量不合格",
                           "source": "asr", "confidence": 0, "confirmed": False})
        data = copy.deepcopy(original)
        data["voice_candidates"] = candidates
        data.pop("voice_pending", None)  # Missing client metadata must not bypass review.
        saved = self.client.put("/api/steel/records/%d" % self.record["id"],
                                json={"version": self.record["version"], "data": data})
        self.assertEqual(saved.status_code, 200, saved.get_json())
        refreshed = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        expected = {item["key"]: item["value"] for item in candidates}
        self.assertEqual({item["key"]: item["value"] for item in refreshed["data"]["voice_candidates"]}, expected)
        self.assertEqual(set(refreshed["data"]["voice_pending"]), set(expected))
        self.assertFalse(refreshed["evaluation"]["can_finalize"])
        self.assertTrue(set(expected) <= {issue["key"] for issue in refreshed["evaluation"]["issues"]})
        for key in ("document_no", "inspection_date", "supply_quantity", "additional_failure_confirmed", "values", "extra_rows"):
            self.assertEqual(refreshed["data"][key], original[key])
        data = copy.deepcopy(refreshed["data"])
        data["voice_pending"] = []
        denied = self.client.post("/api/steel/records/%d/finalize" % self.record["id"],
                                 json={"version": refreshed["version"], "data": data})
        self.assertEqual(denied.status_code, 400)
        self.assertTrue(set(expected) <= {issue["key"] for issue in denied.get_json()["evaluation"]["issues"]})
        # Reviewing/discarding all candidates clears the separate review queue.
        # The official values are still the complete employee-format example.
        data["voice_candidates"] = []
        allowed = self.client.post("/api/steel/records/%d/finalize" % self.record["id"],
                                  json={"version": refreshed["version"], "data": data})
        self.assertEqual(allowed.status_code, 200, allowed.get_json())
        self.assertEqual(allowed.get_json()["record"]["status"], "final")

    def test_unknown_persisted_candidate_is_rejected_without_losing_draft(self):
        data = copy.deepcopy(self.record["data"])
        data["voice_candidates"] = [{"key": "inspector_name", "value": "某人"}]
        denied = self.client.put("/api/steel/records/%d" % self.record["id"],
                                 json={"version": self.record["version"], "data": data})
        self.assertEqual(denied.status_code, 400)
        after = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        self.assertEqual(after, self.record)


if __name__ == "__main__":
    unittest.main(verbosity=2)
