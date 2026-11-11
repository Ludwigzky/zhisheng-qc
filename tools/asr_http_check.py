# -*- coding: utf-8 -*-
"""语音链路的 HTTP 层校验：上传 → 指标 → 降噪分支 → 识别(或明确不可用) → 判分 → 汇总。"""
import io
import json
import os
import sys
import urllib.error
import urllib.request
import http.cookiejar
import uuid

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))
import audio  # noqa: E402
import test_accounts

BASE = test_accounts.isolated_origin()
OUT = os.environ["QC_TEST_OUTPUT"]
fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


class C(object):
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def json(self, path, method="GET", body=None):
        data = json.dumps(body).encode() if body is not None else None
        h = {"Content-Type": "application/json"} if data else {}
        req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
        try:
            with self.op.open(req, timeout=90) as r:
                return json.loads(r.read().decode()), r.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode()), e.code

    def upload(self, path, wav_bytes, form):
        boundary = "----qc" + uuid.uuid4().hex
        body = b""
        for k, v in form.items():
            body += ("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n" % (boundary, k, v)).encode()
        body += ("--%s\r\n" % boundary).encode()
        body += b'Content-Disposition: form-data; name="audio"; filename="say.wav"\r\n'
        body += b"Content-Type: audio/wav\r\n\r\n" + wav_bytes + b"\r\n"
        body += ("--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(BASE + path, data=body, method="POST",
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        try:
            with self.op.open(req, timeout=120) as r:
                return json.loads(r.read().decode()), r.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode()), e.code


def speech_like(dur=1.6, sr=16000):
    t = np.arange(int(sr * dur)) / float(sr)
    f0 = 140 + 20 * np.sin(2 * np.pi * 1.7 * t)
    sig = np.zeros_like(t)
    for k, amp in enumerate([1.0, 0.55, 0.35, 0.22], start=1):
        sig += amp * np.sin(2 * np.pi * k * f0 * t)
    env = np.clip(0.5 * (1 + np.sin(2 * np.pi * 3.1 * t - 1.2)), 0, 1) ** 2
    gate = (np.sin(2 * np.pi * 2.5 * t) > -0.2).astype(np.float32)
    return (sig * env * gate * 0.16).astype(np.float32)


def add_noise(x, snr_db):
    rng = np.random.RandomState(11)
    n = rng.normal(0, 1, len(x)).astype(np.float32)
    p_noise = float(np.mean(n ** 2))
    g = np.sqrt(float(np.mean(x ** 2)) / (10 ** (snr_db / 10.0)) / (p_noise + 1e-12))
    return (x + g * n).astype(np.float32)


x = add_noise(speech_like(), 8.0)
wav_path = os.path.join(OUT, "asr_upload.wav")
audio.write_wav(wav_path, 16000, x)
with open(wav_path, "rb") as f:
    wav_bytes = f.read()

c = C()
_, login_status = c.json("/api/login", "POST", {"emp_no":test_accounts.INSPECTOR,"password":os.environ["QC_TEST_PASSWORD"]})
if login_status != 200:
    raise SystemExit("测试员工密码登录失败")

print("=== 1. 引擎状态 ===")
st, _ = c.json("/api/asr/status")
check("返回引擎信息", "engine" in st, st.get("engine"))
print("     engine=%s available=%s" % (st.get("engine"), st.get("available")))
print("     reason=%s" % (st.get("reason") or "-"))

print("\n=== 2. 上传 + 指标 + 降噪分支 ===")
res, code = c.upload("/api/asr/audio", wav_bytes, {"mode": "system", "denoise": "1"})
check("上传返回 200", code == 200, code)
check("有音频指标", bool(res.get("metrics")), res.get("metrics"))
check("有时长", (res.get("metrics") or {}).get("duration", 0) > 1.0, (res.get("metrics") or {}).get("duration"))
check("有 SNR", (res.get("metrics") or {}).get("snr_db") is not None, (res.get("metrics") or {}).get("snr_db"))
check("降噪分支有指标", bool(res.get("metrics_dn")), res.get("metrics_dn"))
m, mdn = res.get("metrics") or {}, res.get("metrics_dn") or {}
check("降噪后 SNR 提升", (mdn.get("snr_db") or 0) > (m.get("snr_db") or 0),
      "%s -> %s" % (m.get("snr_db"), mdn.get("snr_db")))
check("有两个分支记录", len(res.get("branches") or []) == 2, len(res.get("branches") or []))
check("择优字段存在", res.get("chosen") in ("原声", "降噪"), res.get("chosen"))
print("     branch raw :", (res["branches"][0]["text"], res["branches"][0]["error"]))
print("     branch dn  :", (res["branches"][1]["text"], res["branches"][1]["error"]))
if not res.get("text"):
    check("引擎不可用时给出明确说明", bool(res.get("note")), res.get("note"))
else:
    print("     识别文本:", res.get("text"))

print("\n=== 3. 实验室判分 ===")
lab, code = c.upload("/api/asr/lab/sample", wav_bytes, {"mode": "system", "denoise": "0", "script_id": "s1"})
check("样本入库", code == 200, code)
j = lab.get("judge") or {}
check("返回判分", "ok" in j, j)
print("     ok=%s cer=%s got=%s reason=%s" % (j.get("ok"), j.get("cer"), j.get("got"), j.get("reason")))
check("无文本时判未命中", (j.get("ok") is False) if not (lab.get("result") or {}).get("text") else True,
      j.get("reason"))

print("\n=== 4. 汇总 ===")
summ, _ = c.json("/api/asr/lab/summary")
check("汇总有样本", (summ.get("total") or 0) >= 1, summ.get("total"))
check("有分组统计", len(summ.get("groups") or []) >= 1, summ.get("groups"))
print("     field_accuracy=%s cer=%s snr=%s" % (summ.get("field_accuracy"), summ.get("cer"), summ.get("snr_db")))

print("\n=== 5. 语法约束识别 ===")
boot, _ = c.json("/api/bootstrap")
pv, _ = c.json("/api/plans/%d" % boot["plans"][0]["plan_id"])
tid = pv["nodes"][0]["task_id"]
task, _ = c.json("/api/tasks/%d" % tid)
claim, claim_status = c.json("/api/tasks/%d/claim" % tid, "POST", {"version":task["task"]["version"]})
check("检验员领取本人班组的首道任务", claim_status == 200, claim)
g, code = c.json("/api/tasks/%d/grammar" % tid)
check("语法接口可用", code == 200, code)
check("生成了语法文件", bool(g.get("path")), g.get("path"))
check("语法条目数 > 10", (g.get("items") or 0) > 10, g.get("items"))
print("     ok=%s mode=%s error=%s" % (g.get("ok"), g.get("mode"), (g.get("error") or "-")[:60]))
m, code = c.json("/api/asr/mode", "POST", {"mode": "grammar", "task_id": tid})
check("切换语法模式", code == 200, (code, m.get("mode")))
m2, code = c.json("/api/asr/mode", "POST", {"mode": "dictation", "task_id": tid})
check("切回听写模式", code == 200 and m2.get("mode") == "dictation", (code, m2.get("mode")))
m3, code = c.json("/api/asr/mode", "POST", {"mode": "nonsense"})
check("非法模式被拒", code == 400, code)

r, code = c.upload("/api/asr/audio", wav_bytes,
                   {"mode": "system", "denoise": "0", "asr_mode": "grammar", "task_id": str(tid)})
check("上传时带语法模式", code == 200 and "grammar" in r, list(r.keys())[:8])
check("返回语法状态", bool(r.get("grammar")), r.get("grammar"))

print("\n=== 6. 清理 ===")
_, code = c.json("/api/asr/lab/reset", "POST")
check("清空样本", code == 200, code)
summ2, _ = c.json("/api/asr/lab/summary")
check("清空后为 0", (summ2.get("total") or 0) == 0, summ2.get("total"))

print("\nFAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")
sys.exit(1 if fails else 0)
