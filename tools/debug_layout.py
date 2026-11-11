# -*- coding: utf-8 -*-
"""调试报告排版：打印每个组件之后的 y 位置与页数。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import judge as judge_mod  # noqa: E402
import report as report_mod  # noqa: E402
import seed  # noqa: E402

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
        {"seq": 2, "name": "主梁过程检验（IPQC）", "record": {"judge": judge_mod.judge(seed.TPL_IPQC, vals_ok),
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

doc = report_mod.ReportDoc("检验记录汇总表", "RPT-x", total_pages=None)
print("after header      y=%.1f pages=%d" % (doc.y, len(doc.c.pages)))
doc.meta_block(report_mod._bundle_items(bundle))
print("after meta        y=%.1f pages=%d" % (doc.y, len(doc.c.pages)))
for idx, node in enumerate(bundle["nodes"]):
    doc.section_bar("node %d" % idx)
    rows = node["record"]["judge"]["rows"]
    table_rows = [[(str(i), "#111", "center"), (r["label"], "#111", "left"),
                   (r["spec"], "#666", "left"), (r["value_text"], "#111", "right"),
                   ("合格", "#12855b", "center")] for i, r in enumerate(rows, 1)]
    doc.table(["序号", "检验项目", "规格要求", "实测值", "判定"], table_rows, weights=[26, 140, 150, 95, 56])
    print("after table %d     y=%.1f pages=%d" % (idx, doc.y, len(doc.c.pages)))
doc.callout("检验结论：合格", "sub", color=report_mod.OK, height=48)
print("after callout     y=%.1f pages=%d  limit=%.1f" % (doc.y, len(doc.c.pages), report_mod.H - 58))
