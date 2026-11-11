# -*- coding: utf-8 -*-
"""Inspection verdict vocabulary checks; no real data or model changes."""
import importlib.util
import inspect
import copy
import io
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import asr_sherpa
import asr_verdict
import asr_engine
import app as server
import test_accounts
import db
import seed
import steel
import steel_voice


class VerdictVocabularyChecks(unittest.TestCase):
    def test_exact_verdicts_are_separate_complete_words(self):
        for raw, expected in (("合格", "合格"), ("不合格", "不合格"),
                              (" 不 合 格。 ", "不合格"), ("合 格！", "合格")):
            with self.subTest(raw=raw):
                self.assertEqual(asr_verdict.parse_verdict(raw), expected)
                self.assertFalse(asr_verdict.normalize_candidate(raw)["changed"])

    def test_explicit_whole_homophones_return_explained_review_candidates(self):
        for raw, expected in (("何格", "合格"), ("和格", "合格"), ("不何格", "不合格"),
                              ("不和格", "不合格"), ("布合格", "不合格")):
            with self.subTest(raw=raw):
                candidate = asr_verdict.normalize_candidate(raw, ["合格", "不合格"])
                self.assertEqual(candidate["value"], expected)
                self.assertTrue(candidate["changed"])
                self.assertEqual(candidate["original"], raw)
                self.assertIn(raw, candidate["note"])
                self.assertIn("核对", candidate["note"])

    def test_negation_uncertainty_conflicts_and_unrelated_words_are_never_pass(self):
        rejected = ("没有不合格", "不是不合格", "不能确认合格", "不确定何格", "可能合格", "合格吗",
                    "合格不合格", "不，合格", "不，何格", "合格，不合格", "不不合格", "未发现不合格",
                    "何格或不何格", "合法", "不合法", "合规", "合格率99%", "何格钢铁集团", "请填合格",
                    "合格?", "合格？", "不合格?", "不何格？", "合格" + " " * 40, None, [], 1)
        for raw in rejected:
            with self.subTest(raw=raw):
                self.assertIsNone(asr_verdict.parse_verdict(raw))

    def test_non_verdict_enumerations_cannot_activate_homophone_replacement(self):
        for options in ([], ["合格"], ["不合格"], ["全检", "抽检"], ["有", "无"], "合格不合格", None):
            if options is None:
                continue  # None is the explicit helper default, not a field.
            with self.subTest(options=options):
                self.assertIsNone(asr_verdict.parse_verdict("何格", options))
        self.assertEqual(asr_verdict.parse_verdict("不合格", ["合格", "不合格", "不适用"]), "不合格")

    def test_real_steel_parser_preserves_original_score_and_review_gate(self):
        for key in ("extra_0_verdict", "extra_1_verdict"):
            for raw, expected in (("何格", "合格"), ("不何格", "不合格"), ("不合格", "不合格")):
                with self.subTest(key=key, raw=raw):
                    parsed = steel_voice.parse_utterance(raw, key, asr_confidence=0)
                    self.assertEqual(len(parsed["assignments"]), 1, parsed)
                    candidate = parsed["assignments"][0]
                    self.assertEqual(candidate["value"], expected)
                    self.assertEqual(candidate["raw"], raw)
                    self.assertEqual(candidate["confidence"], 0)
                    self.assertFalse(candidate["confirmed"])
                    self.assertTrue(candidate["requires_confirmation"])
                    if raw != expected:
                        self.assertIn(raw, candidate["note"])
                        self.assertIn(expected, candidate["note"])
                        self.assertIn("核对", candidate["note"])
        for raw in ("不是不合格", "不是合格", "没有不合格", "不确定何格", "合法", "何格或不何格", "合格？"):
            with self.subTest(raw=raw):
                self.assertEqual(steel_voice.parse_utterance(raw, "extra_0_verdict", 0)["assignments"], [])
        for key, _label, _requirement in steel.CHECKS:
            for raw in ("合格", "不合格", "何格", "不何格"):
                with self.subTest(manual_key=key, raw=raw), self.assertRaises(ValueError):
                    steel_voice.parse_utterance(raw, key, 0)
        name = "何格钢铁集团"
        candidate = steel_voice.parse_utterance(name, "manufacturer", 0)["assignments"][0]
        self.assertEqual(candidate["value"], name)
        self.assertEqual(candidate["raw"], name)
        self.assertEqual(candidate["confidence"], 0)

    def test_context_uses_only_verdict_options_and_never_gold_or_numbers(self):
        schema = {"sections": [{"fields": [
            {"key": "surface", "type": "enum", "options": ["合格", "不合格"], "value": "合格", "answer": "不合格"},
            {"key": "thickness", "type": "number", "nominal": 5.75, "min": 5.46},
            {"key": "manufacturer", "type": "text", "value": "何格钢铁集团"},
            {"key": "computed", "type": "enum", "options": ["合格", "不合格"], "derived": True},
        ]}]}
        context = asr_verdict.context(schema, "surface")
        self.assertEqual(context["terms"], ["合格", "不合格"])
        self.assertEqual(context["fields"], ["surface"])
        self.assertFalse(context["acoustic_biasing"])
        for key in ("manufacturer", "thickness", "computed", "missing"):
            self.assertFalse(asr_verdict.context(schema, key)["active"])
        self.assertEqual(asr_verdict.context(schema)["fields"], ["surface"])
        self.assertNotIn("5.75", str(context))
        self.assertNotIn("何格钢铁", str(context))

    def test_fake_decoder_text_and_confidence_are_never_rewritten(self):
        with tempfile.TemporaryDirectory(prefix="qc-verdict-engine-") as directory:
            root = Path(directory)
            (root / "model.int8.onnx").write_bytes(b"mock-model")
            (root / "tokens.txt").write_text("<blank> 0\n何 1\n格 2\n", encoding="utf-8")
            stream = SimpleNamespace(result=SimpleNamespace(text="不何格"), accept_waveform=lambda *_: None)
            recognizer = SimpleNamespace(create_stream=lambda: stream, decode_stream=lambda _: None)
            factory = Mock(return_value=recognizer)
            runtime = SimpleNamespace(OfflineRecognizer=SimpleNamespace(from_paraformer=factory))
            with patch.object(asr_sherpa.importlib, "import_module", return_value=runtime):
                engine = asr_sherpa.SherpaOnnxEngine(root)
            engine.set_context(steel_voice.schema(), "extra_0_verdict")
            wav_path = root / "test.wav"
            with wave.open(str(wav_path), "wb") as wav:
                wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
                wav.writeframes((np.sin(np.arange(16000) * .1) * 5000).astype("<i2").tobytes())
            result = engine.transcribe(str(wav_path))
            self.assertEqual(result["text"], "不何格")
            self.assertEqual(result["confidence"], 0.0)
            self.assertTrue(result["requires_confirmation"])
            self.assertFalse(engine.info()["supports_hotwords"])
            self.assertEqual(engine.info()["active_domain_terms"], ["合格", "不合格"])
            self.assertNotIn("hotwords", factory.call_args.kwargs)
            engine.set_context(steel_voice.schema(), "manufacturer")
            self.assertEqual(engine.info()["active_domain_terms"], [])
            engine.close()

    @unittest.skipUnless(importlib.util.find_spec("sherpa_onnx"), "optional native runtime unavailable")
    def test_installed_paraformer_factory_has_no_acoustic_hotword_parameters(self):
        import sherpa_onnx
        parameters = inspect.signature(sherpa_onnx.OfflineRecognizer.from_paraformer).parameters
        self.assertNotIn("hotwords_file", parameters)
        self.assertNotIn("hotwords_score", parameters)
        self.assertNotIn("hotwords", parameters)


class VerdictApiChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qc-verdict-http-")
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        for name, value in {"DATA_DIR": str(root), "DB_PATH": str(root / "test.db"),
                            "AUDIO_DIR": str(root / "audio"), "REPORT_DIR": str(root / "reports")}.items():
            setting = patch.object(db, name, value)
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
        wav_path = root / "synthetic-input.wav"
        with wave.open(str(wav_path), "wb") as wav:
            wav.setnchannels(1); wav.setsampwidth(2); wav.setframerate(16000)
            wav.writeframes((np.sin(np.arange(16000) * .08) * 6000).astype("<i2").tobytes())
        self.wave_bytes = wav_path.read_bytes()

    def fake_engine(self, text, contexts):
        return SimpleNamespace(name="verdict-test-decoder", available=True,
            info=lambda: {"engine": "verdict-test-decoder", "available": True, "mode": "dictation", "supports_grammar": False},
            set_dictation=lambda: {"ok": True, "mode": "dictation"},
            set_context=lambda schema, current: contexts.append((schema, current)),
            transcribe=lambda _: {"text": text, "confidence": 0, "elapsed_ms": 1, "error": None})

    def test_real_audio_route_and_text_route_share_typed_verdicts_without_mutation(self):
        contexts = []
        for key, raw, expected in (("extra_0_verdict", "何格", "合格"), ("extra_0_verdict", "不何格", "不合格"),
                                    ("extra_0_verdict", "不合格", "不合格"), ("extra_0_verdict", "不是不合格", None),
                                    ("extra_0_verdict", "没有不合格", None), ("extra_0_verdict", "合法", None),
                                    ("extra_0_verdict", "布合格", "不合格"),
                                    ("manufacturer", "何格钢铁集团", "何格钢铁集团")):
            with self.subTest(key=key, raw=raw):
                text_response = self.client.post("/api/steel/asr/parse", json={"text": raw, "current_key": key, "asr_confidence": 0})
                self.assertEqual(text_response.status_code, 200, text_response.get_json())
                with patch.object(asr_engine, "_ENGINE", self.fake_engine(raw, contexts)):
                    audio_response = self.client.post("/api/steel/asr/audio", data={"record_id": str(self.record["id"]),
                        "current_key": key, "asr_mode": "dictation", "audio": (io.BytesIO(self.wave_bytes), "verdict.wav")})
                self.assertEqual(audio_response.status_code, 200, audio_response.get_json())
                text_candidates = text_response.get_json()["assignments"]
                payload = audio_response.get_json()
                self.assertEqual(payload["assignments"], text_candidates)
                self.assertEqual(payload["text"], raw)
                self.assertEqual(payload["branches"][0]["text"], raw)
                self.assertTrue(payload.get("audio_id"))
                self.assertEqual(contexts[-1], (steel_voice.schema(), key))
                if expected is None:
                    self.assertEqual(text_candidates, [])
                else:
                    self.assertEqual(len(text_candidates), 1)
                    candidate = text_candidates[0]
                    self.assertEqual(candidate["value"], expected)
                    self.assertEqual(candidate["raw"], raw)
                    self.assertEqual(candidate["confidence"], 0)
                    self.assertFalse(candidate["confirmed"])
                    self.assertTrue(payload["requires_confirmation"])
                    if raw != expected:
                        self.assertIn(raw, candidate["note"])
        after = self.client.get("/api/steel/records/%d" % self.record["id"]).get_json()["record"]
        self.assertEqual(after, self.record)

    def test_homophone_candidate_remains_review_only_after_save_and_reload(self):
        candidate = self.client.post("/api/steel/asr/parse", json={"text": "不何格", "current_key": "extra_0_verdict", "asr_confidence": 0}).get_json()["assignments"][0]
        data = copy.deepcopy(self.record["data"])
        data["voice_candidates"] = [candidate]
        data.pop("voice_pending", None)
        path = "/api/steel/records/%d" % self.record["id"]
        response = self.client.put(path, json={"version": self.record["version"], "data": data})
        self.assertEqual(response.status_code, 200, response.get_json())
        refreshed = self.client.get(path).get_json()["record"]
        saved = refreshed["data"]["voice_candidates"][0]
        self.assertEqual(saved["value"], "不合格")
        self.assertEqual(saved["raw"], "不何格")
        self.assertEqual(saved["confidence"], 0)
        self.assertFalse(saved["confirmed"])
        self.assertIn("不何格", saved["note"])
        self.assertEqual(refreshed["data"]["values"], self.record["data"]["values"])
        self.assertEqual(refreshed["data"]["voice_pending"], ["extra_0_verdict"])
        finalized = self.client.post(path + "/finalize", json={"version": refreshed["version"], "data": refreshed["data"]})
        self.assertEqual(finalized.status_code, 400)
        self.assertEqual(self.client.get(path).get_json()["record"]["status"], "draft")


if __name__ == "__main__":
    unittest.main(verbosity=2)
