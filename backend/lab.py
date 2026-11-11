# -*- coding: utf-8 -*-
"""识别实验室：固定口报脚本 + 自动判分（字符错误率 / 字段命中）。"""
import math
import re

import cnnum
import judge as judge_mod
import seed

# 与 T-IPQC-02 的字段一一对应，覆盖整数、一位/两位小数、单位、枚举、超差、口误
# say = 目标语义；read = 语法模式下建议的念法（逐位报，命中率最高）
LAB_SCRIPT = [
    {"id": "s1", "say": "跨度 22501", "read": "跨度 二 二 五 零 一", "key": "span", "expect": 22501, "unit": "mm", "kind": "number"},
    {"id": "s2", "say": "对角线L1 22501", "read": "对角线L1 二 二 五 零 一", "key": "diag_l1", "expect": 22501, "unit": "mm", "kind": "number"},
    {"id": "s3", "say": "对角线L2 22500", "read": "对角线L2 二 二 五 零 零", "key": "diag_l2", "expect": 22500, "unit": "mm", "kind": "number"},
    {"id": "s4", "say": "主梁上拱度 26.5", "read": "主梁上拱度 二 六 点 五", "key": "camber", "expect": 26.5, "unit": "mm", "kind": "number"},
    {"id": "s5", "say": "水平度 0.3", "read": "水平度 零 点 三", "key": "level", "expect": 0.3, "unit": "mm/m", "kind": "number"},
    {"id": "s6", "say": "腹板垂直度 1.2", "read": "腹板垂直度 一 点 二", "key": "web_vert", "expect": 1.2, "unit": "mm", "kind": "number"},
    {"id": "s7", "say": "焊缝外观合格", "read": "焊缝外观 合格", "key": "weld", "expect": "合格", "unit": "", "kind": "enum"},
    {"id": "s8", "say": "涂装膜厚 135", "read": "涂装膜厚 一 三 五", "key": "film", "expect": 135, "unit": "μm", "kind": "number"},
    {"id": "s9", "say": "上拱度 18", "read": "上拱度 一 八", "key": "camber", "expect": 18, "unit": "mm", "kind": "number"},
    {"id": "s10", "say": "跨度 1205", "read": "跨度 一 二 零 五", "key": "span", "expect": 1205, "unit": "mm", "kind": "number"},
]

_NORM = re.compile(r"[\s，,。、；;：:！!？?\"'“”‘’（）()【】\[\]]+")
_NUMSEQ = re.compile(u"[零〇一二三四五六七八九十百千万亿两点]+")


def _num_norm(s):
    """把中文数字折成阿拉伯数字，否则「二万二千五百」会把字符错误率算爆。"""
    def rep(m):
        try:
            v = cnnum.cn_to_number(m.group(0))
        except Exception:
            return m.group(0)
        return m.group(0) if v is None else ("%g" % v)
    return _NUMSEQ.sub(rep, cnnum._join_spoken_digits(s or ""))


def norm_text(s):
    return _NORM.sub("", _num_norm(s))


def cer(ref, hyp):
    """字符错误率 = 编辑距离 / 参考长度（先做数字归一，再算）。"""
    a, b = norm_text(ref), norm_text(hyp)
    if not a:
        return 0.0 if not b else 1.0
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        cur = [i]
        for j, cb in enumerate(b, start=1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return round(prev[-1] / float(len(a)), 3)


def fields_of_ipqc():
    return judge_mod.flatten_fields(seed.TPL_IPQC)


def judge_sample(script_id, text):
    """把识别文本按真实口报解析跑一遍，看目标字段是否命中。"""
    item = next((s for s in LAB_SCRIPT if s["id"] == script_id), None)
    if item is None:
        return {"error": "未知脚本项"}
    if not text:
        return {"ok": False, "cer": 1.0, "got": None, "reason": "无识别文本"}
    fields = fields_of_ipqc()
    parsed = cnnum.parse_utterance(text, fields, item["key"])
    hit = next((a for a in parsed["assignments"] if a["key"] == item["key"] and not a.get("derived")), None)
    got = hit["value"] if hit else None
    if item["kind"] == "enum":
        ok = (got == item["expect"])
    else:
        # 字段命中按实际数值等价；不要把接近答案的错误读数算作命中。
        try:
            ok = got is not None and math.isclose(float(got), float(item["expect"]), rel_tol=0, abs_tol=1e-8)
        except (TypeError, ValueError):
            ok = False
    return {
        "ok": bool(ok),
        "cer": cer(item["say"], text),
        "got": got,
        "expect": item["expect"],
        "unit": item.get("unit", ""),
        "raw_text": text,
        "normalized": parsed["normalized"],
        "note": (hit or {}).get("note", "") if hit else "未匹配到目标字段",
        "assignments": [{"key": a["key"], "value": a["value"],
                         "confidence": a["confidence"], "note": a["note"]}
                        for a in parsed["assignments"]],
    }
