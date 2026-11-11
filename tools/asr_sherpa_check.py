# -*- coding: utf-8 -*-
"""Offline adapter contract checks; no sherpa-onnx installation/model required.

Mocks only the native recognizer boundary. Exercises real WAV decoding,
resampling, model selection, initialization failures, result safety and cleanup.
"""
import os
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import asr_sherpa  # noqa: E402


class FakeRecognizer(object):
    def __init__(self):
        self.streams = []
        self.text = " 跨度 二二五零一 "
        self.decode_error = None

    def create_stream(self):
        stream = types.SimpleNamespace(result=types.SimpleNamespace(text=""))

        def accept_waveform(sample_rate, samples):
            stream.sample_rate = sample_rate
            stream.samples = samples

        stream.accept_waveform = accept_waveform
        self.streams.append(stream)
        return stream

    def decode_stream(self, stream):
        if self.decode_error:
            raise RuntimeError(self.decode_error)
        stream.result.text = self.text


class SherpaAdapterChecks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="qc-sherpa-check-")
        self.root = Path(self.temp.name)
        self.model_dir = self.root / "local-model"
        self.model_dir.mkdir()
        (self.model_dir / "model.onnx").write_bytes(b"mock-float-model")
        (self.model_dir / "tokens.txt").write_text("<blank> 0\n跨 1\n", encoding="utf-8")
        self.recognizer = FakeRecognizer()
        self.factory = mock.Mock(return_value=self.recognizer)
        self.module = types.SimpleNamespace(
            OfflineRecognizer=types.SimpleNamespace(from_paraformer=self.factory))
        self.runtime_patch = mock.patch.object(
            asr_sherpa.importlib, "import_module", return_value=self.module)
        self.import_runtime = self.runtime_patch.start()

    def tearDown(self):
        self.runtime_patch.stop()
        self.temp.cleanup()

    def wav(self, name="input.wav", sample_rate=16000, channels=1, empty=False):
        path = self.root / name
        samples = np.zeros(0, dtype="<i2") if empty else (
            np.sin(np.arange(sample_rate) * (2 * np.pi * 180 / sample_rate)) * 8000
        ).astype("<i2")
        if channels == 2:
            samples = np.column_stack((samples, samples))
        with wave.open(str(path), "wb") as wav:
            wav.setnchannels(channels)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(samples.tobytes())
        return str(path)

    def engine(self):
        return asr_sherpa.SherpaOnnxEngine(model_dir=self.model_dir)

    def test_local_factory_arguments_and_quantized_preference(self):
        (self.model_dir / "model.int8.onnx").write_bytes(b"mock-int8-model")
        engine = self.engine()
        self.assertTrue(engine.available, engine.info())
        self.import_runtime.assert_called_once_with("sherpa_onnx")
        self.factory.assert_called_once_with(
            paraformer=str(self.model_dir / "model.int8.onnx"),
            tokens=str(self.model_dir / "tokens.txt"), num_threads=2,
            sample_rate=16000, feature_dim=80, decoding_method="greedy_search",
            debug=False, provider="cpu")

    def test_configured_only_checks_local_assets_without_importing(self):
        self.assertTrue(asr_sherpa.SherpaOnnxEngine.configured(self.model_dir))
        self.assertFalse(asr_sherpa.SherpaOnnxEngine.configured(self.root / "missing"))
        (self.model_dir / "tokens.txt").unlink()
        self.assertFalse(asr_sherpa.SherpaOnnxEngine.configured(self.model_dir))
        self.import_runtime.assert_not_called()

    def test_default_environment_and_float_model_fallback(self):
        with mock.patch.dict(os.environ, {"ASR_SHERPA_MODEL_DIR": str(self.model_dir)}):
            engine = asr_sherpa.SherpaOnnxEngine()
        self.assertTrue(engine.available)
        self.assertEqual(engine.info()["model"], "model.onnx")
        with mock.patch.dict(os.environ, {}, clear=True):
            empty_engine = asr_sherpa.SherpaOnnxEngine()
        self.assertEqual(empty_engine.model_dir, asr_sherpa.DEFAULT_MODEL_DIR)

    def test_missing_and_empty_local_assets_do_not_load_runtime(self):
        for filename in ("model.onnx", "tokens.txt"):
            with self.subTest(filename=filename):
                asset = self.model_dir / filename
                original = asset.read_bytes()
                asset.write_bytes(b"")
                engine = self.engine()
                self.assertFalse(engine.available)
                self.assertTrue(engine.info()["reason"])
                self.import_runtime.assert_not_called()
                result = engine.transcribe("irrelevant.wav")
                self.assertIsNone(result["text"])
                self.assertEqual(result["confidence"], 0.0)
                asset.write_bytes(original)
        missing = asr_sherpa.SherpaOnnxEngine(self.root / "not-present")
        self.assertFalse(missing.available)
        self.import_runtime.assert_not_called()

    def test_missing_runtime_and_bad_model_report_unavailable(self):
        self.import_runtime.side_effect = ImportError("not installed")
        missing = self.engine()
        self.assertFalse(missing.available)
        self.assertIn("not installed", missing.info()["reason"])
        self.factory.assert_not_called()
        self.import_runtime.side_effect = None
        self.factory.side_effect = RuntimeError("invalid ONNX")
        bad = self.engine()
        self.assertFalse(bad.available)
        self.assertIn("invalid ONNX", bad.info()["reason"])

    def test_real_pcm_to_text_never_invents_confidence(self):
        engine = self.engine()
        result = engine.transcribe(self.wav())
        self.assertEqual(result["text"], "跨度 二二五零一")
        self.assertIsNone(result["error"])
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["confidence_kind"], "unavailable")
        self.assertTrue(result["requires_confirmation"])
        stream = self.recognizer.streams[-1]
        self.assertEqual(stream.sample_rate, 16000)
        self.assertEqual(stream.samples.dtype, np.float32)
        self.assertEqual(stream.samples.shape, (16000,))
        self.assertTrue(stream.samples.flags.c_contiguous)
        self.assertGreater(float(np.max(stream.samples)), 0.2)
        self.assertLess(float(np.max(stream.samples)), 0.3)

    def test_stereo_48k_is_mono_16k_and_streams_are_independent(self):
        engine = self.engine()
        path = self.wav(sample_rate=48000, channels=2)
        first = engine.transcribe(path)
        self.recognizer.text = "焊缝外观 合格"
        second = engine.transcribe(path)
        self.assertEqual(first["text"], "跨度 二二五零一")
        self.assertEqual(second["text"], "焊缝外观 合格")
        self.assertEqual(len(self.recognizer.streams), 2)
        self.assertIsNot(self.recognizer.streams[0], self.recognizer.streams[1])
        for stream in self.recognizer.streams:
            self.assertEqual(stream.sample_rate, 16000)
            self.assertEqual(stream.samples.shape, (16000,))

    def test_empty_audio_invalid_audio_and_empty_recognition(self):
        engine = self.engine()
        empty = engine.transcribe(self.wav(empty=True))
        self.assertIsNone(empty["text"])
        self.assertTrue(empty["error"])
        self.assertEqual(len(self.recognizer.streams), 0)
        invalid_path = self.root / "not-a-wav.wav"
        invalid_path.write_bytes(b"not a WAV")
        invalid = engine.transcribe(str(invalid_path))
        self.assertIsNone(invalid["text"])
        self.assertTrue(invalid["error"])
        self.recognizer.text = "  "
        result = engine.transcribe(self.wav())
        self.assertEqual(result["text"], "")
        self.assertIsNone(result["error"])
        self.assertEqual(result["confidence"], 0.0)

    def test_decode_error_is_visible_and_next_request_can_retry(self):
        engine = self.engine()
        self.recognizer.decode_error = "decoder failed"
        result = engine.transcribe(self.wav())
        self.assertIsNone(result["text"])
        self.assertIn("decoder failed", result["error"])
        self.assertTrue(result["requires_confirmation"])
        self.recognizer.decode_error = None
        self.assertIsNone(engine.transcribe(self.wav())["error"])

    def test_capabilities_and_close_are_honest(self):
        engine = self.engine()
        self.assertFalse(engine.load_grammar("form.grxml")["ok"])
        context = engine.set_context({"fields": [{"key": "span"}]}, "span")
        self.assertFalse(context["context_applied"])
        self.assertFalse(engine.info()["supports_grammar"])
        self.assertFalse(engine.info()["supports_hotwords"])
        self.assertTrue(engine.set_dictation()["ok"])
        self.assertEqual(engine.mode, "dictation")
        engine.close()
        engine.close()
        self.assertFalse(engine.available)
        self.assertFalse(engine.set_dictation()["ok"])
        result = engine.transcribe(self.wav())
        self.assertIsNone(result["text"])
        self.assertIn("已关闭", result["error"])
        self.assertEqual(len(self.recognizer.streams), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
