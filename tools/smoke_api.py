# -*- coding: utf-8 -*-
"""端到端冒烟测试：走真实 HTTP，把 6 个演示动作全跑一遍。"""
import json
import os
import sys
import urllib.request
import urllib.error
import http.cookiejar

raise SystemExit("旧版无密码演示流程已停用。请运行 tools/asr_integration_check.py --mobile。")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "_probe")
if not os.path.isdir(OUT):
    os.makedirs(OUT)

fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)) if extra else ""))
    if not cond:
        fails.append(name)


class Client(object):
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, path, method="GET", body=None, raw=False, full=False):
        url = BASE + path
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        r = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.op.open(r, timeout=60) as resp:
                payload = resp.read()
                if raw:
                    return payload, resp.status, dict(resp.headers)
                return json.loads(payload.decode("utf-8")), resp.status
        except urllib.error.HTTPError as e:
            txt = e.read().decode("utf-8", "replace")
            if full:
                raise
            raise RuntimeError("%s %s -> %d %s" % (method, path, e.code, txt[:200]))

    def login(self, emp):
        return self.req("/api/login", "POST", {"emp_no": emp})


def values_from_parse(d):
    out = {}
    for a in d["assignments"]:
        out[a["key"]] = {"value": a["value"], "confidence": a["confidence"],
                         "source": "asr", "raw": a["raw"]}
    return out


print("=== 0. 重置演示数据 ===")
c = Client()
c.req("/api/demo/reset", "POST")
print("  reset ok")

print("=== 1. 检验员登录 / 看计划 ===")
r, _ = c.login("QC1024")
check("登录 QC1024", r["user"]["name"] == "张建国", r["user"]["name"])
boot, _ = c.req("/api/bootstrap")
check("产品计划数", len(boot["plans"]) >= 1, len(boot["plans"]))
plan_id = boot["plans"][0]["plan_id"]
pv, _ = c.req("/api/plans/%d" % plan_id)
check("计划含 3 道工序", len(pv["nodes"]) == 3, [n["name"] for n in pv["nodes"]])
check("初始进度 0/3", pv["progress"]["passed"] == 0, pv["progress"])

print("=== 2. 口报解析（IPQC）===")
ipqc_task = pv["nodes"][1]["task_id"]
p1, _ = c.req("/api/asr/parse", "POST", {
    "task_id": ipqc_task,
    "text": "跨度 22501，对角线L1 22501，对角线L2 22500，主梁上拱度 26.5，水平度 0.3，腹板垂直度 1.2，焊缝外观合格，涂装膜厚 135",
    "current_key": "span"})
got = dict((a["key"], a["value"]) for a in p1["assignments"])
check("解析出 8 项", len(got) == 8, sorted(got.keys()))
check("中文数字/单位", got.get("film") == 135, got.get("film"))
p2, _ = c.req("/api/asr/parse", "POST", {"task_id": ipqc_task, "text": "对角线 1205", "current_key": "span"})
amb = p2["assignments"][0]
check("模糊标签降置信", amb["confidence"] < 0.75, amb)

print("=== 3. 领任务 + 提交判定 ===")
iqc_task = pv["nodes"][0]["task_id"]
c.req("/api/tasks/%d/claim" % iqc_task, "POST")
iqc_vals = {
    "plate_t": {"value": 20.2, "confidence": 1, "source": "asr"},
    "flange_b": {"value": 200, "confidence": 1, "source": "asr"},
    "heat_no": {"value": "K2026-117", "confidence": 1, "source": "manual"},
    "mill_cert": {"value": "齐全", "confidence": 1, "source": "asr"},
    "surface": {"value": "合格", "confidence": 1, "source": "asr"},
}
r1, _ = c.req("/api/tasks/%d/records" % iqc_task, "POST",
              {"values": iqc_vals, "confirmed": [], "transcript": "口报", "engine": "asr"})
check("IQC 判定合格", r1["record"]["judge"]["overall"] == "pass", r1["message"])
check("记录带快照", len(r1["record"]["snapshot"]) >= 8, r1["record"]["snapshot"])

print("=== 4. 超差 → 复测 ===")
bad_vals = dict(values_from_parse(p1))
bad_vals["camber"] = {"value": 18, "confidence": 1.0, "source": "asr"}
r2, _ = c.req("/api/tasks/%d/records" % ipqc_task, "POST",
              {"values": bad_vals, "confirmed": [], "transcript": "上拱度 18", "engine": "asr"})
check("上拱度不足判超差", r2["record"]["judge"]["overall"] == "fail", r2["message"])
check("超差提示具体项", any(x["status"] == "over" for x in r2["record"]["judge"]["rows"]), "")
r3, _ = c.req("/api/tasks/%d/records" % ipqc_task, "POST",
              {"values": values_from_parse(p1), "confirmed": [], "transcript": "复测口报", "engine": "asr"})
check("复测后合格", r3["record"]["judge"]["overall"] == "pass", r3["message"])
check("复测轮次 = 2", r3["record"]["round"] == 2, r3["record"]["round"])
hist = [h["round"] for h in r3["task"]["history"]]
check("历史保留两轮", hist == [1, 2], hist)

print("=== 5. 第三张表 + 齐套 ===")
fqc_task = pv["nodes"][2]["task_id"]
fqc_vals = {
    "hoist_h": {"value": 12005, "confidence": 1, "source": "asr"},
    "static_load": {"value": 12600, "confidence": 1, "source": "asr"},
    "dyn_load": {"value": 11200, "confidence": 1, "source": "asr"},
    "brake_slip": {"value": 1.8, "confidence": 1, "source": "asr"},
    "ins_res": {"value": 5.0, "confidence": 1, "source": "asr"},
    "gnd_res": {"value": 1.2, "confidence": 1, "source": "asr"},
    "limit_ok": {"value": "可靠", "confidence": 1, "source": "asr"},
}
r4, _ = c.req("/api/tasks/%d/records" % fqc_task, "POST",
              {"values": fqc_vals, "confirmed": [], "transcript": "出厂口报", "engine": "asr"})
check("FQC 合格", r4["record"]["judge"]["overall"] == "pass", r4["message"])
pv2, _ = c.req("/api/plans/%d" % plan_id)
check("进度 3/3", pv2["progress"]["passed"] == 3, pv2["progress"])
check("可签字", pv2["can_sign"] is True, pv2["can_sign"])

print("=== 6. 未签字不能出报告 ===")
try:
    c.req("/api/plans/%d/submit" % plan_id, "POST")
    check("未签字被拦截", False, "竟然成功了")
except RuntimeError as e:
    check("未签字被拦截", "签字" in str(e), str(e)[:60])

print("=== 7. 主管签字 → 生成报告 ===")
s = Client()
s.login("QC2001")
sg, _ = s.req("/api/plans/%d/sign" % plan_id, "POST", {"note": "现场记录齐套"})
check("签字成功", any(x["role"] == "supervisor" for x in sg["signoffs"]), len(sg["signoffs"]))
rep, _ = s.req("/api/plans/%d/submit" % plan_id, "POST")
check("生成报告页数", rep["pages"] >= 1, rep)
pdf, status, hdrs = s.req("/api/reports/%d/pdf" % rep["report_id"], raw=True)
check("PDF 可下载", status == 200 and pdf[:4] == b"%PDF", len(pdf))
with open(os.path.join(OUT, "smoke_report.pdf"), "wb") as f:
    f.write(pdf)
png, status2, _ = s.req("/api/reports/%d/preview.png" % rep["report_id"], raw=True)
check("预览 PNG", status2 == 200 and png[:8] == b"\x89PNG\r\n\x1a\n", len(png))
with open(os.path.join(OUT, "smoke_preview.png"), "wb") as f:
    f.write(png)

print("=== 8. 工艺改表 → 新版本 ===")
p = Client()
p.login("PE3001")
tpls, _ = p.req("/api/templates")
ipqc_tpl = [t for t in tpls["templates"] if t["code"] == "T-IPQC-02" and t["active"]][0]
detail, _ = p.req("/api/templates/%d" % ipqc_tpl["id"])
schema = detail["template"]["schema"]
# 把水平度上限从 0.5 收紧到 0.4
for sec in schema["sections"]:
    for f in sec["fields"]:
        if f["key"] == "level":
            f["max"] = 0.4
pub, _ = p.req("/api/templates/%d/publish" % ipqc_tpl["id"], "POST", {"schema": schema})
check("发布新版本", pub["revision"] != detail["template"]["revision"], pub["revision"])
# 用收紧后的规则重判：水平度 0.45 应超差
ipqc_task2 = pv["nodes"][1]["task_id"]
sync, _ = p.req("/api/tasks/%d/sync-template" % ipqc_task2, "POST")
check("任务升级模板", sync.get("ok") is True, sync.get("message"))
pv3, _ = p.req("/api/plans/%d" % plan_id)
rev_now = pv3["nodes"][1]["template_revision"]
check("计划显示新版本", rev_now == pub["revision"], rev_now)

print("=== 9. 留痕 ===")
au, _ = p.req("/api/audit")
acts = [a["action"] for a in au["audits"]]
for need in ("login", "claim_task", "submit_record", "sign_plan", "generate_report", "publish_template"):
    check("留痕包含 " + need, need in acts, "")

print("\n=== 结果 ===")
print("FAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")
