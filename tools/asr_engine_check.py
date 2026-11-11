# -*- coding: utf-8 -*-
"""
识别引擎客户端自检（不依赖真实语音引擎）。

用 mock worker 验证：启动握手、逐行协议、base64 解码、EMPTY/ERR 分支、文本→字段的整条链。
真实 SAPI 只在有权限的机器上跑，但客户端这一层现在就能确定是对的。
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
sys.path.insert(0, BACKEND)

os.environ["ASR_SAPI_WORKER"] = os.path.join(BACKEND, "asr_mock_worker.ps1")

import audio  # noqa: E402
import lab  # noqa: E402
import asr_engine  # noqa: E402

import numpy as np  # noqa: E402

OUT = os.path.join(HERE, "..", "_probe")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


def make_wav(name):
    t = np.arange(16000) / 16000.0
    x = (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)
    p = os.path.join(OUT, name + ".wav")
    audio.write_wav(p, 16000, x)
    return p


print("=== 1. 引擎启动（mock worker）===")
eng = asr_engine.SapiEngine()
check("worker 握手成功", eng.available is True, eng.info())
check("host 为 32 位 PowerShell 或回退", bool(eng.ps), os.path.basename(eng.ps or ""))

print("\n=== 2. 识别协议 ===")
p_ok = make_wav("case_ok")
r = eng.transcribe(p_ok)
check("拿到文本", r["text"] == "跨度 二万二千五百零一", r)
check("置信度透传", abs((r["confidence"] or 0) - 0.93) < 1e-6, r["confidence"])
check("有耗时", r["elapsed_ms"] >= 0, r["elapsed_ms"])

p_empty = make_wav("case_empty")
r2 = eng.transcribe(p_empty)
check("EMPTY 分支", r2["text"] == "" and r2["error"] is None, r2)

p_err = make_wav("case_err")
r3 = eng.transcribe(p_err)
check("ERR 分支带原因", r3["text"] is None and "模拟引擎错误" in (r3["error"] or ""), r3)

print("\n=== 3. 文本 → 字段 → 判分（整条链）===")
parsed = lab.cnnum.parse_utterance(r["text"], lab.fields_of_ipqc(), "span")
span = next((a for a in parsed["assignments"] if a["key"] == "span"), None)
check("中文数字被正确解析", span and span["value"] == 22501.0, span)
j = lab.judge_sample("s1", r["text"])
check("字段判分命中", j["ok"] is True, j)

print("\n=== 4. 无文本时判分不误判 ===")
j2 = lab.judge_sample("s1", "")
check("空文本判未命中", j2["ok"] is False and j2["reason"] == "无识别文本", j2)

print("\n=== 5. 关闭 worker ===")
eng.close()
check("进程已退出", eng.proc is None or eng.proc.poll() is not None, getattr(eng.proc, "poll", lambda: None)())

print("\nFAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")
