# -*- coding: utf-8 -*-
"""打印 IPQC 口报解析与判定明细，定位“待确认”来源。"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

import cnnum  # noqa: E402
import judge as judge_mod  # noqa: E402
import seed  # noqa: E402

TEXT = "跨度 22501，对角线L1 22501，对角线L2 22500，主梁上拱度 26.5，水平度 0.3，腹板垂直度 1.2，焊缝外观合格，涂装膜厚 135"
fields = judge_mod.flatten_fields(seed.TPL_IPQC)
p = cnnum.parse_utterance(TEXT, fields, "span")
for a in p["assignments"]:
    print("%-10s %-14s value=%-8s conf=%.2f  %s" % (a["key"], a["label"], a["value"], a["confidence"], a["note"]))

values = {}
for a in p["assignments"]:
    values[a["key"]] = {"value": a["value"], "confidence": a["confidence"], "source": "asr", "raw": a["raw"]}
res = judge_mod.judge(seed.TPL_IPQC, values)
print("\noverall:", res["overall"], res["counts"])
for r in res["rows"]:
    if r["status"] != "ok":
        print("  ", r["key"], r["status"], r["note"], r["confidence"], r["flag"])
