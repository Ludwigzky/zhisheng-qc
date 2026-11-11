# -*- coding: utf-8 -*-
"""后端核心自检：口报解析 / 判定 / 报告排版。不启动 HTTP。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import cnnum  # noqa: E402
import judge as judge_mod  # noqa: E402
import report as report_mod  # noqa: E402
import seed  # noqa: E402
from pdf_doc import pick_font  # noqa: E402
import verify_pdf  # noqa: E402

OUT = os.path.join(HERE, "..", "_probe")
if not os.path.isdir(OUT):
    os.makedirs(OUT)

fails = []


def check(name, got, want):
    ok = got == want
    if not ok:
        fails.append("%s: got %r want %r" % (name, got, want))
    print("%-34s %s  %r" % (name, "OK " if ok else "BAD", got))


print("=== 1. 中文数字与单位 ===")
check("十二点五", cnnum.cn_to_number("十二点五"), 12.5)
check("一千二百零五", cnnum.cn_to_number("一千二百零五"), 1205.0)
check("两千两百", cnnum.cn_to_number("两千两百"), 2200.0)
check("零点三", cnnum.cn_to_number("零点三"), 0.3)
check("点五", cnnum.cn_to_number("点五"), 0.5)
check("负零点零三", cnnum.cn_to_number("负零点零三"), -0.03)
check("十五", cnnum.cn_to_number("十五"), 15.0)
check("一百零五", cnnum.cn_to_number("一百零五"), 105.0)
check("22501", cnnum.cn_to_number("22501"), 22501.0)

print("\n=== 2. 口报槽位抽取（IPQC）===")
ipqc_fields = judge_mod.flatten_fields(seed.TPL_IPQC)
base = [f for f in ipqc_fields if not f.get("derived")]

r1 = cnnum.parse_utterance("跨度 22501，对角线差 1.2，主梁上拱度 26.5，水平度 0.3", base)
got1 = {a["key"]: a["value"] for a in r1["assignments"]}
check("四字段抽取", (len(got1), got1.get("camber"), got1.get("level")), (4, 26.5, 0.3))
print("     note:", [a["note"] for a in r1["assignments"]])

r2 = cnnum.parse_utterance("上拱度十八", base)
got2 = {a["key"]: a["value"] for a in r2["assignments"]}
check("中文数字口报", got2.get("camber"), 18.0)

r3 = cnnum.parse_utterance("膜厚一百三十微米", base)
got3 = {a["key"]: a["value"] for a in r3["assignments"]}
check("单位识别", got3.get("film"), 130.0)

r4 = cnnum.parse_utterance("焊缝外观合格", base)
got4 = {a["key"]: a["value"] for a in r4["assignments"]}
check("枚举口报", got4.get("weld"), "合格")

r5 = cnnum.parse_utterance("对角线 1205", base)
amb = r5["assignments"][0] if r5["assignments"] else {}
check("模糊标签降置信", (amb.get("confidence") or 1) < 0.75, True)

print("\n=== 3. 判定引擎 ===")
vals_ok = {
    "span": {"value": 22501, "confidence": 1.0, "source": "manual"},
    "diag_l1": {"value": 22501, "confidence": 1.0, "source": "manual"},
    "diag_l2": {"value": 22500, "confidence": 1.0, "source": "manual"},
    "camber": {"value": 26.5, "confidence": 1.0, "source": "manual"},
    "level": {"value": 0.3, "confidence": 1.0, "source": "manual"},
    "web_vert": {"value": 1.2, "confidence": 1.0, "source": "manual"},
    "weld": {"value": "合格", "confidence": 1.0, "source": "manual"},
    "film": {"value": 135, "confidence": 1.0, "source": "manual"},
}
j_ok = judge_mod.judge(seed.TPL_IPQC, vals_ok)
check("全合格", j_ok["overall"], "pass")
check("派生量对角线差", [r["value_text"] for r in j_ok["rows"] if r["key"] == "diag_delta"], ["1.0"])

vals_bad = dict(vals_ok)
vals_bad["camber"] = {"value": 18, "confidence": 1.0, "source": "manual"}
j_bad = judge_mod.judge(seed.TPL_IPQC, vals_bad)
check("上拱度不足 → 超差", j_bad["overall"], "fail")

vals_typo = dict(vals_ok)
vals_typo["span"] = {"value": 1205, "confidence": 0.7, "source": "asr"}
j_typo = judge_mod.judge(seed.TPL_IPQC, vals_typo)
check("疑似口误 → 待确认", j_typo["overall"], "pending")
j_typo2 = judge_mod.judge(seed.TPL_IPQC, vals_typo, confirmed=["span"])
check("确认后 → 超差", j_typo2["overall"], "fail")

vals_missing = {k: v for k, v in vals_ok.items() if k != "film"}
j_missing = judge_mod.judge(seed.TPL_IPQC, vals_missing)
check("缺项", j_missing["overall"], "pending")

print("\n=== 4. 报告排版 ===")
bundle = {
    "plan": {"code": "ITP-WH2026-0111-01", "std_ref": "GB/T 3811-2008 / 技术协议 TA-2026-011 v3"},
    "product": {"code": "WH2026-0111", "name": "桥式起重机", "model": "LD10t-22.5m",
                "customer": "中车洛阳机车", "contract_no": "HT-2026-0071"},
    "nodes": [
        {"seq": 1, "name": "来料检验（IQC）", "record": {"judge": judge_mod.judge(seed.TPL_IQC, {
            "plate_t": {"value": 20.2, "confidence": 1.0, "source": "manual"},
            "flange_b": {"value": 200, "confidence": 1.0, "source": "manual"},
            "heat_no": {"value": "K2026-117", "confidence": 1.0, "source": "manual"},
            "mill_cert": {"value": "齐全", "confidence": 1.0, "source": "manual"},
            "surface": {"value": "合格", "confidence": 1.0, "source": "manual"}}),
            "template_code": "T-IQC-01", "template_revision": "v1.3",
            "operator_name": "张建国", "created_at": "2026-11-09 09:21:00", "snapshot": "a1b2c3"}},
        {"seq": 2, "name": "主梁过程检验（IPQC）", "record": {"judge": j_ok,
            "template_code": "T-IPQC-02", "template_revision": "v2.0",
            "operator_name": "张建国", "created_at": "2026-11-09 14:22:00", "snapshot": "d4e5f6"}},
        {"seq": 3, "name": "出厂检验（FQC）", "record": {"judge": judge_mod.judge(seed.TPL_FQC, {
            "hoist_h": {"value": 12005, "confidence": 1.0, "source": "manual"},
            "static_load": {"value": 12600, "confidence": 1.0, "source": "manual"},
            "dyn_load": {"value": 11200, "confidence": 1.0, "source": "manual"},
            "brake_slip": {"value": 1.8, "confidence": 1.0, "source": "manual"},
            "ins_res": {"value": 5.0, "confidence": 1.0, "source": "manual"},
            "gnd_res": {"value": 1.2, "confidence": 1.0, "source": "manual"},
            "limit_ok": {"value": "可靠", "confidence": 1.0, "source": "manual"}}),
            "template_code": "T-FQC-03", "template_revision": "v1.1",
            "operator_name": "张建国", "created_at": "2026-11-09 16:40:00", "snapshot": "9z8y7x"}},
    ],
    "signoffs": [{"role": "supervisor", "name": "李慧敏", "at": "2026-11-09 17:05:00"}],
    "generated_at": "2026-11-09 17:10",
    "report_no": "RPT-WH2026-0111-01",
    "snapshot": "a1b2c3-d4e5f6-9z8y7x",
}
pdf_path = os.path.join(OUT, "report.pdf")
path, pages = report_mod.build_report(bundle, pdf_path)
check("PDF 页数 >= 1", pages >= 1, True)
print("     pdf:", path, os.path.getsize(path), "bytes,", pages, "pages")

with open(os.path.join(OUT, "report_p1.png"), "wb") as f:
    f.write(verify_pdf.render_from_pdf(pdf_path, pick_font(), page_no=1))
if pages > 1:
    with open(os.path.join(OUT, "report_p2.png"), "wb") as f:
        f.write(verify_pdf.render_from_pdf(pdf_path, pick_font(), page_no=2))

# 结构校验（复用画布做宽度比对）
doc = report_mod._compose(bundle, total_pages=pages)
problems, stats = verify_pdf.verify(pdf_path, doc.c, doc.pdf) if pages == 1 else ([], {})
print("     verify(p1 only when single page):", stats, "problems:", len(problems))
for p in problems[:10]:
    print("       -", p)

print("\n=== 结果 ===")
print("FAILS:", len(fails))
for f in fails:
    print(" -", f)
print("OK" if not fails else "FAIL")
