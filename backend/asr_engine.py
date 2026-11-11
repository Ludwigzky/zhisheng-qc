# -*- coding: utf-8 -*-
"""
本地 ASR 适配层（可插拔，全离线）。

引擎：ASR_ENGINE 显式选择；auto 依次尝试本地 Paraformer、SAPI、Whisper、stub。
识别模式：
  grammar    SAPI 语法约束：表单/当前字段的标签、数字、单位及枚举
  dictation  自由听写：整句开放识别（灵活但容易胡言乱语）
接口统一：transcribe(wav) -> {text, confidence, engine, elapsed_ms, error}
"""
import base64
import inspect
import math
import os
import queue
import subprocess
import threading
import time

import asr_settings

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
SAPI_SCRIPT = os.environ.get("ASR_SAPI_WORKER") or os.path.join(HERE, "asr_sapi_worker.ps1")
DEFAULT_MODEL_DIR = os.path.join(ROOT, "models", "faster-whisper-small")

PS32 = r"C:\Windows\SysWOW64\WindowsPowerShell\v1.0\powershell.exe"
PS64 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

MODE_GRAMMAR = "grammar"
MODE_DICTATION = "dictation"
SESSION_LOCK = threading.RLock()


def context_terms(schema, current_key=None):
    """仅用字段名、单位与枚举作提示；绝不把标准值/评测答案喂给模型。"""
    terms = []
    for section in (schema or {}).get("sections", []):
        for field in section.get("fields", []):
            if field.get("derived") or (current_key and field.get("key") != current_key):
                continue
            names = [field.get("label"), field.get("unit")]
            names += str(field.get("syn") or "").replace("／", "/").split("/")
            names += field.get("options") or []
            for name in names:
                if name and str(name) not in terms:
                    terms.append(str(name))
    return terms


class StubEngine(object):
    name = "stub"
    available = False
    mode = "stub"

    def __init__(self, reason="未接入识别引擎"):
        self.reason = reason
        self.grammar = None
        self.grammar_error = reason

    def info(self):
        return {"engine": self.name, "available": False, "reason": self.reason,
                "mode": "stub", "grammar": None, "grammar_error": self.grammar_error,
                "supports_grammar": False}

    def transcribe(self, wav_path):
        return {"text": None, "confidence": 0.0, "engine": self.name,
                "elapsed_ms": 0, "error": self.reason}

    def load_grammar(self, path):
        return {"ok": False, "error": self.reason, "mode": "stub"}

    def set_dictation(self):
        return {"ok": True, "mode": "stub"}

    def close(self):
        pass


class SapiEngine(object):
    """Windows SAPI 中文识别（常驻 worker，逐行协议）。"""
    name = "sapi"

    def __init__(self):
        self.proc = None
        self.q = None
        self.lock = threading.Lock()
        self.error = None
        self.available = False
        self.ps = None
        self.mode = MODE_DICTATION
        self.grammar = None
        self.grammar_error = None
        self._boot()

    # ---- 启动 ----
    def _candidates(self):
        out = []
        if os.path.exists(PS32):
            out.append(PS32)
        if os.path.exists(PS64):
            out.append(PS64)
        out.append("powershell.exe")
        return out

    def _boot(self):
        if not os.path.exists(SAPI_SCRIPT):
            self.error = "缺少 asr_sapi_worker.ps1"
            return
        for ps in self._candidates():
            try:
                proc = subprocess.Popen(
                    [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", SAPI_SCRIPT],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", bufsize=1,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as exc:
                self.error = "启动 PowerShell 失败: %s" % exc
                continue
            q = queue.Queue()
            threading.Thread(target=self._reader, args=(proc, q), daemon=True).start()
            try:
                first = q.get(timeout=25)
            except queue.Empty:
                self.error = "识别引擎启动超时"
                try:
                    proc.kill()
                except Exception:
                    pass
                continue
            if first == "READY":
                self.proc, self.q, self.ps, self.available, self.error = proc, q, ps, True, None
                return
            if first.startswith("FAIL\t"):
                self.error = self._decode(first.split("\t", 1)[1]) or "引擎初始化失败"
            else:
                self.error = "引擎启动异常输出: %s" % str(first)[:120]
            try:
                proc.kill()
            except Exception:
                pass
        self.available = False

    @staticmethod
    def _reader(proc, q):
        try:
            for line in proc.stdout:
                q.put(line.rstrip("\r\n"))
        except Exception:
            pass
        q.put("__EOF__")

    @staticmethod
    def _decode(b64):
        try:
            return base64.b64decode(b64.encode("ascii")).decode("utf-8", "replace")
        except Exception:
            return ""

    def info(self):
        return {"engine": self.name, "available": bool(self.available),
                "reason": self.error, "host": os.path.basename(self.ps or ""),
                "lang": "zh-CN", "mode": self.mode,
                "supports_grammar": True,
                "grammar": os.path.basename(self.grammar) if self.grammar else None,
                "grammar_error": self.grammar_error}

    # ---- 指令 ----
    def _alive(self):
        return self.available and self.proc is not None and self.proc.poll() is None

    def _command(self, line, timeout=30):
        """发一条指令并读一行回复。返回 (kind, parts)。"""
        with self.lock:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            try:
                reply = self.q.get(timeout=timeout)
            except queue.Empty:
                self.available = False
                self.error = "引擎无响应（超时），请重新检测"
                self.proc.kill()  # 不允许下一条请求读到本次超时的迟到回复
                raise RuntimeError("引擎无响应（超时）")
        if reply in ("__EOF__", ""):
            self.available = False
            self.error = "worker 已退出"
            raise RuntimeError(self.error)
        return reply.split("\t")

    def load_grammar(self, path):
        if not self._alive():
            return {"ok": False, "error": self.error or "引擎不可用", "mode": self.mode}
        try:
            parts = self._command("LOAD " + os.path.abspath(path), timeout=40)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "mode": self.mode}
        if parts[0] == "GRAMMAR" and len(parts) > 1 and parts[1] == "OK":
            self.mode = MODE_GRAMMAR
            self.grammar = os.path.abspath(path)
            self.grammar_error = None
            return {"ok": True, "mode": self.mode, "grammar": os.path.basename(path)}
        msg = self._decode(parts[2]) if len(parts) > 2 else "语法加载失败"
        self.grammar_error = msg
        self.mode = MODE_DICTATION
        self.grammar = None
        return {"ok": False, "error": msg, "mode": self.mode}

    def set_dictation(self):
        if not self._alive():
            return {"ok": False, "error": self.error or "引擎不可用", "mode": self.mode}
        try:
            parts = self._command("DICTATION", timeout=20)
        except Exception as exc:
            return {"ok": False, "error": str(exc), "mode": self.mode}
        if not (len(parts) >= 2 and parts[0] == "GRAMMAR" and parts[1] == "DICTATION"):
            msg = self._decode(parts[2]) if len(parts) > 2 else "听写模式加载失败"
            self.grammar_error = msg
            return {"ok": False, "error": msg, "mode": self.mode}
        self.mode = MODE_DICTATION
        self.grammar = None
        self.grammar_error = None
        return {"ok": True, "mode": self.mode}

    def transcribe(self, wav_path):
        if not self._alive():
            return {"text": None, "confidence": 0.0, "engine": self.name, "elapsed_ms": 0,
                    "error": self.error or "引擎不可用"}
        t0 = time.time()
        try:
            parts = self._command(os.path.abspath(wav_path), timeout=30)
        except Exception as exc:
            return {"text": None, "confidence": 0.0, "engine": self.name,
                    "elapsed_ms": 0, "error": str(exc)}
        ms = int((time.time() - t0) * 1000)
        kind = parts[0]
        if kind == "OK" and len(parts) >= 3:
            conf = float(parts[1] or 0)
            return {"text": self._decode(parts[2]), "confidence": round(conf, 3),
                    "engine": self.name, "elapsed_ms": ms, "error": None, "mode": self.mode}
        if kind == "EMPTY":
            return {"text": "", "confidence": 0.0, "engine": self.name,
                    "elapsed_ms": ms, "error": None, "mode": self.mode}
        msg = self._decode(parts[2]) if len(parts) > 2 else "识别失败"
        return {"text": None, "confidence": 0.0, "engine": self.name,
                "elapsed_ms": ms, "error": msg, "mode": self.mode}

    def close(self):
        try:
            if self.proc and self.proc.poll() is None:
                self.proc.stdin.write("QUIT\n")
                self.proc.stdin.flush()
                self.proc.wait(timeout=3)
        except Exception:
            pass


class FasterWhisperEngine(object):
    name = "faster-whisper"
    available = True
    mode = "dictation"

    def __init__(self):
        self.error = None
        self.model = None
        self.grammar = None
        self.grammar_error = "该引擎不支持语法约束"
        self.model_dir = os.environ.get("ASR_MODEL_DIR", DEFAULT_MODEL_DIR)
        self.terms = []
        self.available = (os.path.isfile(os.path.join(self.model_dir, "model.bin"))
                          and os.path.isfile(os.path.join(self.model_dir, "config.json")))
        if not self.available:
            self.error = "缺少本地 faster-whisper 模型: %s" % self.model_dir

    def info(self):
        return {"engine": self.name, "available": self.available, "reason": self.error,
                "model_dir": self.model_dir, "lang": "zh", "mode": self.mode,
                "grammar": None, "grammar_error": self.grammar_error,
                "supports_grammar": False,
                "confidence_kind": "decoder_score_uncalibrated"}

    def set_context(self, schema, current_key=None):
        self.terms = context_terms(schema, current_key)

    def load_grammar(self, path):
        return {"ok": False, "error": self.grammar_error, "mode": self.mode}

    def set_dictation(self):
        return {"ok": True, "mode": self.mode}

    def _load(self):
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model = WhisperModel(self.model_dir, device="cpu", compute_type="int8",
                                      local_files_only=True)
        return self.model

    def transcribe(self, wav_path):
        t0 = time.time()
        if not self.available:
            return {"text": None, "confidence": 0.0, "engine": self.name,
                    "elapsed_ms": 0, "error": self.error}
        try:
            model = self._load()
            options = dict(language="zh", beam_size=5, vad_filter=True,
                           temperature=0, condition_on_previous_text=False,
                           initial_prompt="质检术语：" + "、".join(self.terms))
            if "hotwords" in inspect.signature(model.transcribe).parameters:
                options["hotwords"] = " ".join(self.terms)
            segments, _info = model.transcribe(wav_path, **options)
            segments = [s for s in segments if s.text.strip()]
            text = "，".join(s.text.strip() for s in segments)
            scores = [math.exp(min(0.0, float(s.avg_logprob))) *
                      (1.0 - max(0.0, min(1.0, float(s.no_speech_prob)))) for s in segments]
            conf = min(scores) if scores else 0.0
            return {"text": text, "confidence": round(conf, 3), "engine": self.name,
                    "confidence_kind": "decoder_score_uncalibrated",
                    "elapsed_ms": int((time.time() - t0) * 1000), "error": None}
        except Exception as exc:
            return {"text": None, "confidence": 0.0, "engine": self.name,
                    "elapsed_ms": int((time.time() - t0) * 1000), "error": str(exc)}

    def close(self):
        pass


# --------------------------------------------------------------------------
_ENGINE = None


def _has_faster_whisper():
    try:
        import faster_whisper  # noqa: F401
        return True
    except Exception:
        return False


def _select_engine(force=None):
    global _ENGINE
    if _ENGINE is not None and force is None:
        return _ENGINE
    selected = force or os.environ.get("ASR_ENGINE")
    if not selected:
        try:
            selected = asr_settings.read_settings().get("engine", "auto")
        except (ValueError, OSError) as exc:
            _ENGINE = StubEngine("语音配置无法读取: %s" % exc)
            return _ENGINE
    selected = str(selected).strip().lower()
    if selected == "stub":
        eng = StubEngine("手动指定 stub")
    elif selected == "sherpa-onnx":
        from asr_sherpa import SherpaOnnxEngine
        eng = SherpaOnnxEngine()
    elif selected == "faster-whisper":
        eng = FasterWhisperEngine() if _has_faster_whisper() else StubEngine("未安装 faster-whisper")
    elif selected == "sapi":
        eng = SapiEngine() if os.name == "nt" else StubEngine("SAPI 仅支持 Windows")
    elif selected != "auto":
        eng = StubEngine("未知 ASR_ENGINE: %s" % selected)
    else:
        eng = None
    if selected != "auto":
        if force is None:
            _ENGINE = eng
        return eng
    from asr_sherpa import SherpaOnnxEngine
    if SherpaOnnxEngine.configured():
        portable = SherpaOnnxEngine()
        if portable.available:
            _ENGINE = portable
            return portable
    if os.name == "nt":
        eng = SapiEngine()
        if eng.available:
            _ENGINE = eng
            return eng
        if _has_faster_whisper():
            whisper = FasterWhisperEngine()
            if whisper.available:
                _ENGINE = whisper
                return _ENGINE
        _ENGINE = StubEngine(eng.error or "Windows 识别引擎不可用")
        return _ENGINE
    if _has_faster_whisper():
        whisper = FasterWhisperEngine()
        _ENGINE = whisper if whisper.available else StubEngine(whisper.error)
        return _ENGINE
    _ENGINE = StubEngine("当前系统没有可用引擎")
    return _ENGINE


def get_engine(force=None):
    with SESSION_LOCK:
        return _select_engine(force)


def set_context(schema, current_key=None):
    eng = get_engine()
    if hasattr(eng, "set_context"):
        eng.set_context(schema, current_key)


def engine_name():
    return get_engine().name


def available():
    return bool(get_engine().available)


def transcribe(wav_path):
    return get_engine().transcribe(wav_path)


def load_grammar(path):
    with SESSION_LOCK:
        return get_engine().load_grammar(path)


def set_dictation():
    with SESSION_LOCK:
        return get_engine().set_dictation()


def status():
    eng = get_engine()
    info = eng.info()
    info["engines"] = [
        {"engine": "sherpa-onnx", "note": "本地 Paraformer ONNX；同模型可供 Android 使用，需实机评测"},
        {"engine": "sapi", "note": "Windows zh-CN 识别器；优先尝试 32 位宿主，实际可用性需自检"},
        {"engine": "faster-whisper", "note": "装了模型才可用；方言/术语需微调"},
        {"engine": "stub", "note": "只做录音留档与质量分析"},
    ]
    info["advice"] = _advice(info)
    return info


def _installed_recognizers():
    if os.name != "nt":
        return []
    script = ("Add-Type -AssemblyName System.Speech; "
              "[System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() | "
              "ForEach-Object { $_.Culture.Name }")
    for ps in (PS32, PS64):
        if not os.path.exists(ps):
            continue
        try:
            out = subprocess.run([ps, "-NoProfile", "-Command", script],
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 timeout=25, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            lines = [l.strip() for l in out.stdout.decode("utf-8", "replace").splitlines() if l.strip()]
            if lines:
                return lines
        except Exception:
            continue
    return []


def _advice(info):
    if info.get("available"):
        if info.get("engine") == "sherpa-onnx":
            return "普通话离线识别已就绪：选中字段后说一项，核对转写和读数再确认。"
        if info.get("mode") == MODE_GRAMMAR:
            return "引擎就绪（语法约束模式）：按当前字段标签、数字、单位和枚举识别，结果仍需核对。"
        return "引擎就绪（自由听写模式）：一项一句，保留原始录音；请用实验室比较字段命中率。"
    reason = str(info.get("reason") or "")
    if ("Access is denied" in reason or "E_ACCESSDENIED" in reason or "0x80070005" in reason
            or "找不到 SAPI" in reason or "语音识别不可用" in reason):
        return ("系统里有识别器，但当前服务进程拿不到语音引擎（多见于沙箱 / 受限账户 / 服务方式启动）。"
                "请在桌面终端先跑 check-asr.bat 自检，再用 start.bat 或 run-https.bat 启动服务。")
    if "超时" in reason:
        return "引擎启动超时，可能是 PowerShell 首启太慢；重试一次或先手动跑 check-asr.bat。"
    return "没有可用的本地识别引擎；录音仍会留档并计算音频指标，文本可用键盘或示例补。"


def _probe(reset=True):
    global _ENGINE
    if reset:
        old = _ENGINE
        if old is not None and hasattr(old, "close"):
            try:
                old.close()
            except Exception:
                pass
        _ENGINE = None
    info = status()
    info["recognizers"] = _installed_recognizers() if info.get("engine") == "sapi" else []
    info["checked_at"] = time.strftime("%H:%M:%S")
    return info


def probe(reset=True):
    with SESSION_LOCK:
        return _probe(reset)
