# -*- coding: utf-8 -*-
"""
判定引擎：把「表单结构 + 实测值」算成「逐项合格 / 超差 / 缺项 / 待确认」。

要点
  * 表达式来自模板，用白名单 AST 求值，杜绝模板里跑任意代码
  * 判定结果连同「模板版本 + 标准版本 + 规则快照」一起落库，事后可复现
  * 疑似口误（偏离基准过大）优先判“待确认”，而不是直接判“超差”
"""
import ast
import hashlib
import json
import math

ALLOWED_FUNCS = {
    "abs": abs, "min": min, "max": max, "round": round,
    "sqrt": math.sqrt, "pow": pow,
    "avg": lambda *a: sum(a) / float(len(a)),
}
ALLOWED_NODES = (
    ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load,
    ast.Call, ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Pow, ast.USub, ast.UAdd,
    ast.Mod, ast.FloorDiv, ast.Compare, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.Eq, ast.NotEq, ast.IfExp, ast.BoolOp, ast.And, ast.Or, ast.Not,
    ast.Tuple, ast.List,
)


def safe_eval(expr, env):
    tree = ast.parse(expr, mode="eval")
    for node in ast.walk(tree):
        if not isinstance(node, ALLOWED_NODES):
            raise ValueError("模板表达式含不允许的语法: %s" % type(node).__name__)
        if isinstance(node, ast.Name) and node.id not in env and node.id not in ALLOWED_FUNCS:
            raise ValueError("未知变量: %s" % node.id)
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in ALLOWED_FUNCS:
                raise ValueError("不允许的函数调用")
    return eval(compile(tree, "<rule>", "eval"), {"__builtins__": {}}, dict(ALLOWED_FUNCS, **env))


def flatten_fields(schema):
    out = []
    for sec in schema.get("sections", []):
        for f in sec.get("fields", []):
            g = dict(f)
            g["section"] = sec.get("name", "")
            g["derived"] = False
            out.append(g)
    for r in schema.get("rules", []):
        g = dict(r)
        g["section"] = g.get("section", "计算结果")
        g["type"] = "number"
        g["derived"] = True
        g["required"] = False
        out.append(g)
    return out


def _fmt(v, decimals=None):
    if v is None:
        return "—"
    if isinstance(v, str):
        return v
    if decimals is None:
        return ("%g" % v) if isinstance(v, float) else str(v)
    return ("%." + str(int(decimals)) + "f") % v


def spec_text(f):
    if f.get("spec"):
        return f["spec"]
    if f.get("type") == "enum":
        opts = f.get("options") or []
        ok = f.get("pass") or []
        return "合格项：" + "、".join(ok) if ok else " / ".join(opts)
    if f.get("type") == "text":
        return f.get("hint") or "必填"
    unit = f.get("unit") or ""
    dec = f.get("decimals")
    if f.get("nominal") is not None and f.get("tol") is not None:
        return "%s ± %s %s" % (_fmt(f["nominal"], dec), _fmt(f["tol"]), unit)
    if f.get("min") is not None and f.get("max") is not None:
        return "%s ~ %s %s" % (_fmt(f["min"], dec), _fmt(f["max"], dec), unit)
    if f.get("min") is not None:
        return "≥ %s %s" % (_fmt(f["min"], dec), unit)
    if f.get("max") is not None:
        return "≤ %s %s" % (_fmt(f["max"], dec), unit)
    return unit


def snapshot_hash(schema, values):
    payload = json.dumps({
        "template": schema.get("code"),
        "revision": schema.get("revision"),
        "std_version": schema.get("std_version"),
        "values": {k: v.get("value") for k, v in values.items()},
    }, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def judge(schema, values, confirmed=None):
    """
    values: {key: {"value": 数字或字符串, "confidence": 0~1, "raw": 原文, "source": "asr|manual"}}
    confirmed: 已人工确认的 key 集合
    """
    confirmed = set(confirmed or [])
    fields = flatten_fields(schema)
    rows = []
    env = {}

    # 1) 先把基础数值灌进求值环境，再算派生量（跨字段计算）
    for f in fields:
        if f.get("derived"):
            continue
        meta = values.get(f["key"]) or {}
        v = meta.get("value")
        try:
            if isinstance(v, bool):
                continue
            if isinstance(v, (int, float)):
                env[f["key"]] = float(v)
            elif isinstance(v, str) and v.strip():
                env[f["key"]] = float(v)
        except (TypeError, ValueError):
            continue

    for f in fields:
        if not f.get("derived"):
            continue
        expr = f.get("expr")
        if not expr:
            continue
        try:
            v = safe_eval(expr, env)
            env[f["key"]] = float(v)
        except Exception as exc:
            env[f["key"]] = None
            f["_err"] = str(exc)

    for f in fields:
        key = f["key"]
        meta = values.get(key) or {}
        raw_value = env.get(key) if f.get("derived") else meta.get("value")
        try:
            conf = 1.0 if f.get("derived") else float(meta.get("confidence", 1.0 if meta.get("source") == "manual" else 0.0))
            if not math.isfinite(conf) or not 0 <= conf <= 1:
                conf = 0.0
        except (TypeError, ValueError):
            conf = 0.0
        status, note, flag = "ok", "", ""

        if raw_value is None or raw_value == "":
            if f.get("required"):
                status, note = "missing", "必填项未采集"
            else:
                status = "na"
        elif f.get("type") == "enum":
            pass_set = f.get("pass") or []
            if pass_set and raw_value not in pass_set:
                status, note = "over", "不符合合格项：%s" % raw_value
            else:
                note = ""
        elif f.get("type") == "text":
            status = "ok"
        else:
            try:
                v = float(raw_value)
                if isinstance(raw_value,bool) or not math.isfinite(v):
                    raise ValueError("需要有限数值")
            except (TypeError, ValueError, OverflowError):
                status, note = "missing", "不是有效数值"
                v = None
            if v is not None:
                lo, hi = f.get("min"), f.get("max")
                if f.get("nominal") is not None and f.get("tol") is not None:
                    lo, hi = f["nominal"] - f["tol"], f["nominal"] + f["tol"]
                nominal = f.get("nominal")
                if nominal:
                    dev = abs(v - nominal) / float(abs(nominal) or 1)
                    if dev > 0.2:
                        flag = "suspicious"
                        note = "数值与基准 %s 偏差 %s%%，疑似口误" % (_fmt(nominal, f.get("decimals")), round(dev * 100, 1))
                if lo is not None and v < lo:
                    status = "over"
                    note = note or ("低于下限 %s" % _fmt(lo, f.get("decimals")))
                elif hi is not None and v > hi:
                    status = "over"
                    note = note or ("高于上限 %s" % _fmt(hi, f.get("decimals")))
                elif flag != "suspicious":
                    status = "ok"
                if lo is not None and hi is not None and lo <= v <= hi:
                    status = "ok" if flag != "suspicious" else status

        # 低置信 / 疑似口误：未确认一律转“待确认”
        if status in ("ok", "over") and key not in confirmed:
            if flag == "suspicious" or conf < 0.75:
                status = "pending"
                if not note:
                    note = "识别评分不足或不可用，请核对" if conf == 0 else "识别评分 %d%%，请核对" % int(conf * 100)

        if f.get("derived"):
            unit_note = "（按 %s 计算）" % f.get("expr", "")
            note = (note + unit_note).strip()

        rows.append({
            "key": key,
            "label": f.get("label", key),
            "section": f.get("section", ""),
            "type": f.get("type", "number"),
            "unit": f.get("unit", ""),
            "spec": spec_text(f),
            "value": raw_value,
            "value_text": _fmt(raw_value, f.get("decimals")),
            "status": status,
            "note": note,
            "flag": flag,
            "confidence": round(conf, 2),
            "confirmed": key in confirmed,
            "derived": bool(f.get("derived")),
            "required": bool(f.get("required")),
        })

    counts = {
        "ok": sum(1 for r in rows if r["status"] == "ok"),
        "over": sum(1 for r in rows if r["status"] == "over"),
        "pending": sum(1 for r in rows if r["status"] == "pending"),
        "missing": sum(1 for r in rows if r["status"] == "missing"),
        "na": sum(1 for r in rows if r["status"] == "na"),
    }
    if counts["over"]:
        overall = "fail"
    elif counts["pending"] or counts["missing"]:
        overall = "pending"
    else:
        overall = "pass"

    return {
        "overall": overall,
        "rows": rows,
        "counts": counts,
        "template": {"code": schema.get("code"), "revision": schema.get("revision"),
                     "title": schema.get("title"), "std_version": schema.get("std_version"),
                     "spec_ref": schema.get("spec_ref")},
        "snapshot": snapshot_hash(schema, values),
    }


def summary_line(result):
    c = result["counts"]
    if result["overall"] == "pass":
        return "合格（%d 项全部符合）" % c["ok"]
    if result["overall"] == "fail":
        return "超差（%d 项不合格，%d 项待确认）" % (c["over"], c["pending"])
    return "待完善（%d 项待确认，%d 项缺项）" % (c["pending"], c["missing"])
