# -*- coding: utf-8 -*-
"""ASR 请求隔离、静音、分支冲突及引擎分数回归；不改演示数据库。"""
import io
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import app as server
import asr_engine
import audio
import lab
import seed
import asr_benchmark


class PipelineChecks(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        for name in ("DATA_DIR", "AUDIO_DIR", "REPORT_DIR"):
            p = patch.object(server.db, name, self.tmp.name)
            p.start()
            self.addCleanup(p.stop)
        p = patch.object(server, "GRAMMAR_DIR", self.tmp.name)
        p.start()
        self.addCleanup(p.stop)
        self.eng = Mock(available=True, mode="dictation", grammar=None)
        self.eng.name = "test"
        self.eng.info.return_value = {"engine": "test", "available": True}
        self.eng.set_dictation.return_value = {"ok": True, "mode": "dictation"}
        self.eng.load_grammar.return_value = {"ok": True, "mode": "grammar"}
        p = patch.object(asr_engine, "_ENGINE", self.eng)
        p.start()
        self.addCleanup(p.stop)

    def process(self, results, samples=None, denoise=False, schema=seed.TPL_IPQC, **form):
        if samples is None:
            samples = 0.2 * np.sin(np.arange(16000) * 0.06)
        path = os.path.join(self.tmp.name, "input.wav")
        audio.write_wav(path, 16000, samples)
        with open(path, "rb") as f:
            payload = f.read()
        self.eng.transcribe.side_effect = results
        data = dict(form, audio=(io.BytesIO(payload), "say.wav"))
        with server.app.test_request_context("/api/asr/audio", method="POST", data=data):
            try:
                return server._process_audio(denoise, schema)
            finally:
                server.request.environ["wsgi.input"].close()

    def test_silence_does_not_decode_or_hallucinate(self):
        result = self.process([], samples=np.zeros(16000))
        self.assertIn("静音", result["note"])
        self.eng.transcribe.assert_not_called()

    def test_conflicting_digits_require_confirmation(self):
        result = self.process([
            {"text": "跨度22501", "confidence": .96},
            {"text": "跨度22502", "confidence": .98},
        ], denoise=True, current_key="span")
        self.assertTrue(result["branch_conflict"])
        self.assertTrue(result["requires_confirmation"])
        self.assertLess(result["confidence"], .75)

    def test_equivalent_readings_do_not_conflict(self):
        result = self.process([
            {"text": "跨度22501", "confidence": .96},
            {"text": "跨度二万二千五百零一毫米", "confidence": .93},
        ], denoise=True, current_key="span")
        self.assertFalse(result["branch_conflict"])
        self.assertEqual(result["chosen"], "原声")

    def test_unavailable_confidence_stays_zero(self):
        result = self.process([{"text": "跨度22501", "confidence": 0.0,
                                "confidence_kind": "unavailable", "requires_confirmation": True}])
        self.assertEqual(result["confidence"], 0)
        self.assertTrue(result["requires_confirmation"])

    def test_no_task_clears_previous_grammar(self):
        self.process([{"text": "你好", "confidence": .8}], schema=None)
        self.eng.set_dictation.assert_called_once()

    def test_clipping_requires_confirmation(self):
        result = self.process([{"text": "跨度22501", "confidence": .99}],
                              samples=np.ones(16000))
        self.assertTrue(result["requires_confirmation"])
        self.assertIn("削波", result["note"])

    def test_overlong_audio_is_rejected_without_decoding(self):
        result = self.process([], samples=np.ones(16000 * 31) * .1)
        self.assertIn("30 秒", result["error"])
        self.eng.transcribe.assert_not_called()

    def test_restarted_engine_does_not_reuse_stale_grammar_cache(self):
        first = server.ensure_grammar(seed.TPL_IPQC, "span")
        server._GRAMMAR_STATE.update(path=first["path"], mode="grammar")
        server.ensure_grammar(seed.TPL_IPQC, "span")
        self.assertEqual(self.eng.load_grammar.call_count, 2)

    def test_context_excludes_nominal_and_gold(self):
        terms = asr_engine.context_terms(seed.TPL_IPQC, "span")
        self.assertIn("跨度", terms)
        self.assertNotIn("22500", " ".join(terms))
        self.assertNotIn("上拱度", terms)

    def test_http_parse_propagates_zero_and_rejects_invalid_score(self):
        with patch.object(server, "me", return_value={"id": 1}), \
                patch.object(server.db, "row", return_value={"template_id": 1}), \
                patch.object(server, "tpl_of", return_value={"schema": seed.TPL_IPQC}):
            client = server.app.test_client()
            response = client.post("/api/asr/parse", json={"task_id": 1, "text": "跨度22501",
                                                         "current_key": "span", "asr_confidence": 0})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["assignments"][0]["confidence"], 0)
            invalid = client.post("/api/asr/parse", json={"task_id": 1, "asr_confidence": "nan"})
            self.assertEqual(invalid.status_code, 400)

    def test_zero_score_requires_judgement_confirmation(self):
        values = {"span": {"value": 22501, "confidence": 0, "source": "asr"}}
        result = server.judge_mod.judge(seed.TPL_IPQC, values)
        self.assertEqual(next(r for r in result["rows"] if r["key"] == "span")["status"], "pending")
        result = server.judge_mod.judge(seed.TPL_IPQC, values, confirmed=["span"])
        self.assertEqual(next(r for r in result["rows"] if r["key"] == "span")["status"], "ok")

    def test_lab_does_not_credit_nearby_wrong_values(self):
        self.assertFalse(lab.judge_sample("s1", "跨度22501.4")["ok"])
        self.assertEqual(lab.cer("主梁上拱度26.5", "主梁上拱度 二 六 点 五"), 0)

    def test_benchmark_replay_scores_wrong_auto_fill_without_answer_leak(self):
        path = os.path.join(self.tmp.name, "sample.wav")
        audio.write_wav(path, 16000, .2 * np.sin(np.arange(16000) * .06))
        manifest = os.path.join(self.tmp.name, "manifest.jsonl")
        sample = {"wav": "sample.wav", "template": "ipqc", "current_key": "span",
                  "reference": "跨度22501", "expected": {"span": 22501}}
        with open(manifest, "w", encoding="utf-8") as f:
            f.write(json.dumps(sample) + "\n" + json.dumps(sample))
        self.eng.transcribe.side_effect = [{"text": "跨度22501", "confidence": .96},
                                          {"text": "跨度22502", "confidence": .96}]
        output = os.path.join(self.tmp.name, "report.json")
        args = SimpleNamespace(manifest=manifest, engine="sapi", mode="dictation", denoise=False, output=output)
        asr_benchmark.run(args)
        with open(output, encoding="utf-8") as f:
            report = json.load(f)
        self.assertEqual(report["summary"]["field_accuracy"], .5)
        self.assertEqual(report["summary"]["false_auto_fields"], 1)
        for call in self.eng.set_context.call_args_list:
            self.assertNotIn("22501", json.dumps(call.args, ensure_ascii=False))

    def test_whisper_uses_decoder_scores_and_current_terms(self):
        model = Mock()
        model.transcribe.return_value = ([SimpleNamespace(text="跨度22501", avg_logprob=-1.0,
                                                          no_speech_prob=.2)], None)
        with patch.object(os.path, "isfile", return_value=True):
            engine = asr_engine.FasterWhisperEngine()
        engine.model = model
        engine.set_context(seed.TPL_IPQC, "span")
        result = engine.transcribe("unused.wav")
        self.assertLess(result["confidence"], .5)
        self.assertNotEqual(result["confidence"], .9)
        options = model.transcribe.call_args.kwargs
        self.assertFalse(options["condition_on_previous_text"])
        self.assertNotIn("22500", options["initial_prompt"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
