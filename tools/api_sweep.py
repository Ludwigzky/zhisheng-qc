# -*- coding: utf-8 -*-
"""把每个 GET 接口都打一遍，确认没有 500 / NameError 这类低级错误。"""
import json
import os
import sys
import urllib.error
import urllib.request
import http.cookiejar
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import test_accounts

BASE = test_accounts.isolated_origin()
fails = []


class C(object):
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def get(self, path):
        req = urllib.request.Request(BASE + path)
        try:
            with self.op.open(req, timeout=60) as r:
                raw = r.read()
                return r.status, raw, r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as e:
            return e.code, e.read(), e.headers.get("Content-Type", "")

    def post(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else b""
        req = urllib.request.Request(BASE + path, data=data, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with self.op.open(req, timeout=60) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)[:160]) if extra else ""))
    if not cond:
        fails.append(name)


c = C()
st, body = c.post("/api/login", {"emp_no":test_accounts.INSPECTOR,"password":os.environ["QC_TEST_PASSWORD"]})
check("登录", st == 200, st)

boot = json.loads(c.get("/api/bootstrap")[1])
plan_id = boot["plans"][0]["plan_id"]
pv = json.loads(c.get("/api/plans/%d" % plan_id)[1])
task_id = pv["nodes"][0]["task_id"]

routes = [
    "/api/me", "/api/bootstrap", "/api/products" if False else "/api/bootstrap",
    "/api/plans/%d" % plan_id,
    "/api/tasks/%d" % task_id,
    "/api/templates",
    "/api/templates/1",
    "/api/audit",
    "/api/audit?target=task:%d" % task_id,
    "/api/notifications",
    "/api/asr/status",
    "/api/asr/lab/script",
    "/api/asr/lab/summary",
    "/app.js", "/style.css", "/",
]

print("=== GET 接口 ===")
for r in routes:
    st, body, ct = c.get(r)
    ok = st == 200
    if r.endswith(".js") or r.endswith(".css") or r == "/":
        ok = ok and not body.strip().startswith(b'{"error"')
    else:
        try:
            j = json.loads(body)
            if isinstance(j, dict) and j.get("error"):
                ok = False
        except Exception:
            ok = False
    check("GET " + r, ok, "%s %s" % (st, body[:80]))

print("\n=== 关键接口字段 ===")
st, body, _ = c.get("/api/asr/lab/script")
j = json.loads(body)
check("实验室脚本非空", st == 200 and len(j.get("script") or []) == 10, len(j.get("script") or []))
check("实验室脚本带模板信息", bool((j.get("template") or {}).get("code")),
      (j.get("template") or {}).get("code"))
check("实验室脚本带引擎信息", "engine" in (j.get("engine") or {}), j.get("engine"))

st, body, _ = c.get("/api/asr/lab/summary")
j2 = json.loads(body)
check("汇总接口结构完整", st == 200 and "field_accuracy" in j2 and "groups" in j2, list(j2.keys())[:6])

print("\nFAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")
sys.exit(1 if fails else 0)
