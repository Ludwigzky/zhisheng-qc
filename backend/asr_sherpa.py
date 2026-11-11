# -*- coding: utf-8 -*-
"""Local offline Paraformer adapter for sherpa-onnx; never downloads models.

Only sherpa-onnx's exported *offline* Paraformer model is supported. The same
model/tokens pair can later be packaged with its Android runtime. This adapter
does not add SRGS grammars, hotword biasing, or a fabricated confidence score.

API reference:
https://github.com/k2-fsa/sherpa-onnx/blob/master/sherpa-onnx/python/sherpa_onnx/offline_recognizer.py
"""
import importlib
import os
import threading
import time

import numpy as np

import audio
import asr_settings
import asr_verdict


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DEFAULT_MODEL_DIR = os.path.join(ROOT, "models", "paraformer-zh")
GRAMMAR_ERROR = "离线 Paraformer 适配器不支持 SRGS 语法约束或热词偏置"


class SherpaOnnxEngine(object):
    name = "sherpa-onnx"
    mode = "dictation"

    @staticmethod
    def _resolve_model_dir(model_dir=None):
        configured_dir = model_dir or os.environ.get("ASR_SHERPA_MODEL_DIR") or asr_settings.model_directory()
        return os.path.abspath(os.path.expanduser(os.fspath(configured_dir)))

    @classmethod
    def configured(cls, model_dir=None):
        """Only inspect local paths; importing the optional runtime is deferred."""
        directory = cls._resolve_model_dir(model_dir)
        return os.path.isfile(os.path.join(directory, "tokens.txt")) and any(
            os.path.isfile(os.path.join(directory, filename))
            for filename in ("model.int8.onnx", "model.onnx"))

    def __init__(self, model_dir=None):
        self.model_dir = self._resolve_model_dir(model_dir)
        self.model_path = None
        self.tokens_path = os.path.join(self.model_dir, "tokens.txt")
        self.available = False
        self.error = None
        self.grammar = None
        self.grammar_error = GRAMMAR_ERROR
        self._recognizer = None
        self.runtime_version = None
        self.num_threads = 2
        self.model_id = os.path.basename(self.model_dir)
        self.verdict_context = asr_verdict.context(None)
        self._lock = threading.Lock()
        self._initialize()

    def _initialize(self):
        """Validate local assets before importing or creating a native runtime."""
        for filename in ("model.int8.onnx", "model.onnx"):
            candidate = os.path.join(self.model_dir, filename)
            if os.path.isfile(candidate) and os.path.getsize(candidate) > 0:
                self.model_path = candidate
                break
        if self.model_path is None:
            self.error = "缺少本地离线 Paraformer 模型: %s (model.int8.onnx 或 model.onnx)" % self.model_dir
            return
        if not os.path.isfile(self.tokens_path) or os.path.getsize(self.tokens_path) == 0:
            self.error = "缺少或为空的本地 tokens.txt: %s" % self.tokens_path
            return
        try:
            sherpa_onnx = importlib.import_module("sherpa_onnx")
            version = getattr(sherpa_onnx, "__version__", None)
            self.runtime_version = version if isinstance(version, str) else None
        except Exception as exc:
            self.error = "无法导入 sherpa-onnx，请先安装匹配本机的运行库: %s" % exc
            return
        try:
            self.num_threads = int(os.environ.get("ASR_NUM_THREADS") or
                                   asr_settings.read_settings().get("num_threads", 2))
            if not 1 <= self.num_threads <= 16:
                raise ValueError("ASR_NUM_THREADS 必须在 1 到 16 之间")
            metadata = os.path.join(self.model_dir, "model-info.json")
            if os.path.isfile(metadata):
                import json
                with open(metadata, encoding="utf-8") as source:
                    self.model_id = json.load(source).get("model_id") or self.model_id
            # `paraformer`, not `model`, is the official factory's keyword.
            # Keep CPU/greedy settings common to the later Android integration.
            self._recognizer = sherpa_onnx.OfflineRecognizer.from_paraformer(
                paraformer=self.model_path,
                tokens=self.tokens_path,
                num_threads=self.num_threads,
                sample_rate=16000,
                feature_dim=80,
                decoding_method="greedy_search",
                debug=False,
                provider="cpu",
            )
        except Exception as exc:
            self.error = "本地离线 Paraformer 初始化失败: %s" % exc
            return
        self.available = True

    def info(self):
        return {
            "engine": self.name,
            "available": bool(self.available),
            "reason": self.error,
            "model_dir": self.model_dir,
            "model": os.path.basename(self.model_path) if self.model_path else None,
            "model_id": self.model_id,
            "runtime_version": self.runtime_version,
            "num_threads": self.num_threads,
            "lang": "zh",
            "mode": self.mode,
            "grammar": None,
            "grammar_error": self.grammar_error,
            "supports_grammar": False,
            "supports_hotwords": False,
            "domain_terms": list(asr_verdict.TERMS),
            "domain_term_mode": "strict_verdict_candidates",
            "domain_term_note": "合格/不合格用于判定字段候选归一；当前 Paraformer 不支持声学热词偏置，原始转写与置信度保持不变",
            "active_domain_terms": list(self.verdict_context["terms"]),
            "confidence_kind": "unavailable",
            "requires_confirmation": True,
        }

    def load_grammar(self, path):
        return {"ok": False, "error": self.grammar_error, "mode": self.mode}

    def set_dictation(self):
        return {"ok": bool(self.available), "mode": self.mode, "error": self.error}

    def set_context(self, schema, current_key=None):
        """Keep honest field vocabulary for downstream candidate parsing."""
        with self._lock:
            self.verdict_context = asr_verdict.context(schema, current_key)
        return {"ok": False, "context_applied": False,
                "error": self.grammar_error, "mode": self.mode,
                "postprocess_context": dict(self.verdict_context)}

    def _result(self, started, text=None, error=None):
        return {
            "text": text,
            # Offline Paraformer does not expose a calibrated utterance
            # confidence here. Zero deliberately routes values to confirmation.
            "confidence": 0.0,
            "confidence_kind": "unavailable",
            "requires_confirmation": True,
            "engine": self.name,
            "mode": self.mode,
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "error": error,
        }

    def transcribe(self, wav_path):
        started = time.monotonic()
        # Serialize access to the native recognizer. Each request gets a new
        # stream so no audio or text leaks into the next utterance.
        with self._lock:
            if not self.available or self._recognizer is None:
                return self._result(started, error=self.error or "引擎不可用")
            try:
                sample_rate, samples = audio.read_wav(wav_path)
                if len(samples) == 0:
                    return self._result(started, error="音频为空")
                sample_rate, samples = audio.to_mono16k(sample_rate, samples)
                samples = np.ascontiguousarray(samples, dtype=np.float32)
                if samples.ndim != 1 or not np.isfinite(samples).all():
                    raise ValueError("音频必须是有限数值的单声道 PCM")
                stream = self._recognizer.create_stream()
                stream.accept_waveform(sample_rate, samples)
                self._recognizer.decode_stream(stream)
                text = stream.result.text.strip()
                return self._result(started, text=text)
            except Exception as exc:
                return self._result(started, error="离线 Paraformer 识别失败: %s" % exc)

    def close(self):
        with self._lock:
            self._recognizer = None
            self.available = False
            self.error = "引擎已关闭"
