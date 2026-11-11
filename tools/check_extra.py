# -*- coding: utf-8 -*-
"""补充检查：音频上传、通知、若干拒绝路径。"""
import io
import json
import os
import sys
import urllib.request
import urllib.error
import http.cookiejar
import uuid

raise SystemExit("旧版演示重置测试已停用。请运行 tools/asr_integration_check.py。")
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
            with self.op.open(req, timeout=30) as r:
                return json.loads(r.read().decode()), r.status
        except urllib.error.HTTPError as e:
            return json.loads(e.read().decode()), e.code

    def raw(self, path):
        req = urllib.request.Request(BASE + path)
        with self.op.open(req, timeout=30) as r:
            return r.read(), r.status, r.headers.get("Content-Type")

    def upload(self, path, content, filename="a.wav"):
        boundary = "----qc" + uuid.uuid4().hex
        body = b""
        body += ("--%s\r\n" % boundary).encode()
        body += ('Content-Disposition: form-data; name="audio"; filename="%s"\r\n' % filename).encode()
        body += b"Content-Type: audio/wav\r\n\r\n" + content + b"\r\n"
        body += ("--%s--\r\n" % boundary).encode()
        req = urllib.request.Request(BASE + path, data=body, method="POST",
                                     headers={"Content-Type": "multipart/form-data; boundary=" + boundary})
        with self.op.open(req, timeout=30) as r:
            return json.loads(r.read().decode()), r.status


c = C()
c.json("/api/demo/reset", "POST")          # 从干净状态开始，避免受其它用例影响
c.json("/api/login", "POST", {"emp_no": "QC1024"})

print("=== 音频上传（无 ASR 模型）===")
wav = b"RIFF" + (36 + 800).to_bytes(4, "little") + b"WAVEfmt " + (16).to_bytes(4, "little") + \
      (1).to_bytes(2, "little") + (1).to_bytes(2, "little") + (16000).to_bytes(4, "little") + \
      (32000).to_bytes(4, "little") + (2).to_bytes(2, "little") + (16).to_bytes(2, "little") + \
      b"data" + (800).to_bytes(4, "little") + b"\x00" * 800
d, st = c.upload("/api/asr/audio", wav)
check("上传成功", st == 200 and d.get("audio_id"), d)
check("引擎为 stub 时 text 为 None", d.get("text") is None, d.get("note"))
aid = d.get("audio_id", "")
body, st2, ct = c.raw("/api/audio/" + aid)
check("音频可取回", st2 == 200 and len(body) == len(wav), (st2, len(body)))

print("=== 通知 ===")
n, _ = c.json("/api/notifications")
check("通知接口可用", isinstance(n.get("notifications"), list), len(n.get("notifications", [])))

print("=== 拒绝路径 ===")
boot, _ = c.json("/api/bootstrap")
plan_id = boot["plans"][0]["plan_id"]
pv, _ = c.json("/api/plans/%d" % plan_id)
tid = pv["nodes"][0]["task_id"]
r, code = c.json("/api/plans/%d/submit" % plan_id, "POST")
check("未齐套拒绝出报告", code == 400, r.get("error"))
r, code = c.json("/api/reports/999/pdf")
check("报告不存在 404", code == 404, r.get("error"))
r, code = c.json("/api/login", "POST", {"emp_no": "NOPE"})
check("未知工号 404", code == 404, r.get("error"))

print("=== 权限 ===")
c2 = C()
c2.json("/api/login", "POST", {"emp_no": "QC1024"})
r, code = c2.json("/api/plans/%d/sign" % plan_id, "POST", {})
check("检验员不能签字（403）", code == 403, (code, r.get("error")))
c3 = C()
c3.json("/api/login", "POST", {"emp_no": "PE3001"})
r, code = c3.json("/api/templates/%d/publish" % 1, "POST", {"schema": {}})
check("空模板被拒（400）", code == 400, (code, r.get("error")))

print("\nFAILS:", len(fails), fails)
print("OK" if not fails else "FAIL")
