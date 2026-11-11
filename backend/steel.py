# -*- coding: utf-8 -*-
"""Steel incoming-inspection stage, mapped to the employee's HW/Q-JL-02A form.

Limits below come from this supplied specimen, not a claim that they apply to
every steel size. The inspector must use the requirements for the current lot.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re

RECORD_CODE = "HW/Q-JL-02A"
VERIFY = [
    ("cert_provided", "质量证明书", "供应商应提供生产厂钢板、钢带的质量证明书"),
    ("cert_performance", "证明书性能指标", "提供的质量证明书上钢板、钢带的各项性能指标应符合国家标准GB/T709或GB/T1591有关要求"),
    ("identity_match", "实物与证明书一致", "实物的规格、牌号、炉批号等应与提供的质量证明书及测试委托单上的要求相符"),
    ("cert_seal", "质量证明专用章", "质量证明书上应盖有供货单位质量证明专用章"),
]
APPEARANCE = [
    ("surface", "表面质量", "表面不得有肉眼可见的夹杂、折叠、结疤、裂纹轧制及严重划伤等缺陷。冷轧钢板及钢带表面应光滑美观（t＜4）"),
    ("shape", "外形波浪变形", "钢板外形不应有明显的波浪变形"),
    ("coil", "成卷牢固及塔高", "钢带应牢固地成卷，塔高符合标准GB/T709的要求"),
]
_SAMPLING_NOTE_REMAINDER = (
    "b)批次数量大于50件，抽取8%进行检验，不少于5件，但不超过30件。\n"
    "c)抽检过程中发现不合格品应加倍抽检，在追加检验中仍有不合格品则对该批货物进行 100%检验或拒收或评审后按评审结果执行。"
)
SAMPLING_NOTE = "a)1～4件可抽检，至少抽1件；5～50件抽取10%，不少于5件。\n" + _SAMPLING_NOTE_REMAINDER
LEGACY_SAMPLING_NOTE = "a)批次数量小于等于50件，抽取10%进行检验，但不少于5件。\n" + _SAMPLING_NOTE_REMAINDER
INSPECTOR_LABEL = "质量工程师"
CHECKS = VERIFY + APPEARANCE
FIELD_KEYS = {x[0] for x in CHECKS} | {"thickness", "width"}
HEADER_TEXT = ("document_no", "product_name", "specification", "manufacturer", "grade", "heat_no", "inspection_date", "sampling_basis", "disposition")
NUMBER_KEYS = ("supply_quantity", "inspected_quantity", "qualified_quantity", "rejected_quantity",
               "thickness_nominal", "thickness_lower", "thickness_upper", "width_nominal", "width_lower", "width_upper")
DISPOSITIONS = ("加倍抽检", "转全检", "拒收", "评审后处理")


def blank_data():
    return {"document_no": "", "product_name": "钢卷", "specification": "5.75*1800", "manufacturer": "", "grade": "", "heat_no": "",
            "supply_quantity": 1, "inspection_date": datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d"),
            "thickness_nominal": 5.75, "thickness_lower": -.29, "thickness_upper": .29,
            "width_nominal": 1800, "width_lower": 0, "width_upper": 25,
            "inspection_mode": "sampling", "inspected_quantity": 1, "qualified_quantity": 1, "rejected_quantity": 0,
            "sampling_basis": "本表备注抽样规则", "disposition": "", "values": {}, "extra_rows": [], "evidence": [],
            "is_example": False, "additional_failure_confirmed": False,
            "voice_pending": [], "voice_candidates": []}


def employee_example():
    data = blank_data()
    data.update(document_no="2603389", manufacturer="邯钢集团", grade="Q355B", inspection_date="2026-03-24", inspection_mode="full", sampling_basis="原表备注抽样规则")
    data["is_example"] = True
    data["values"] = {key: {"value": "合格", "source": "manual", "confidence": 1, "confirmed": True} for key, _, _ in CHECKS}
    data["values"].update(thickness={"value": [5.64], "source": "manual", "confidence": 1, "confirmed": True},
                          width={"value": [1811], "source": "manual", "confidence": 1, "confirmed": True})
    return data


def followup_data(data):
    """Carry the lot and its requirements into a new, unperformed inspection."""
    result = normalize(data)
    result.update(inspection_date=blank_data()["inspection_date"], values={}, evidence=[],
                  voice_pending=[], voice_candidates=[], inspected_quantity=None,
                  qualified_quantity=None, rejected_quantity=None, disposition="",
                  additional_failure_confirmed=False)
    result["extra_rows"] = [{"name": row["name"], "requirement": row["requirement"],
                             "result": "", "verdict": ""} for row in result["extra_rows"]]
    return result


def numeric(value):
    if value is None or value == "" or isinstance(value, bool):
        raise ValueError("需要有效数值")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("需要有效有限数值")
    if not math.isfinite(number):
        raise ValueError("数值必须有限")
    return number


def _voice_review(body):
    """Validate persisted, unapplied voice candidates independently of readings."""
    pending = body.get("voice_pending", [])
    candidates = body.get("voice_candidates", [])
    if not isinstance(pending, list) or len(pending) > 37:
        raise ValueError("待确认语音字段必须为数组，最多37项")
    if not isinstance(candidates, list) or len(candidates) > 37:
        raise ValueError("语音候选必须为数组，最多37项")
    if not pending and not candidates:
        return [], []
    # Local import avoids a module cycle: steel_voice's catalog uses CHECKS.
    import steel_voice
    targets = steel_voice.TARGETS
    result_keys, result_candidates = [], {}

    def known_key(key):
        if not isinstance(key, str) or key not in targets:
            raise ValueError("未知待确认语音字段")
        return key

    def safe_value(value, target):
        if value is None:
            return None  # A cleared review input can be saved and corrected later.
        if isinstance(value, str):
            limit = 80 if target["destination"] == "extra" else 160
            if len(value) > limit:
                raise ValueError("语音候选内容过长，请缩短后保存")
            if target["kind"] == "boolean" and value not in ("true", "false", ""):
                raise ValueError("语音布尔候选只能为是或否")
            if target["kind"] == "enum" and value and value not in target.get("options", []):
                raise ValueError("语音结论候选不在该字段选项中")
            return value
        if isinstance(value, bool):
            if target["kind"] != "boolean":
                raise ValueError("此语音候选不能使用布尔值")
            return value
        if isinstance(value, (int, float)):
            if target["kind"] != "number":
                raise ValueError("文本、日期和编号语音候选须保留为文本")
            return numeric(value)
        if isinstance(value, list) and target["destination"] == "value" and target["kind"] == "number":
            if len(value) > 12:
                raise ValueError("每项语音候选最多12个读数")
            if any(isinstance(item, (dict, list, bool)) or item is None for item in value):
                raise ValueError("语音候选读数格式无效")
            return [safe_value(item, target) for item in value]
        raise ValueError("语音候选值必须为文本、有限数值、布尔或测量读数数组")

    for key in pending:
        key = known_key(key)
        if key not in result_keys:
            result_keys.append(key)
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("每个语音候选必须是字段对象")
        key = known_key(candidate.get("key"))
        target = targets[key]
        if "value" not in candidate:
            raise ValueError("语音候选缺少待核对内容")
        raw = candidate.get("raw", "")
        note = candidate.get("note", "")
        if not isinstance(raw, str) or len(raw) > 2000 or not isinstance(note, str) or len(note) > 240:
            raise ValueError("语音候选原话或说明格式无效或过长")
        confidence = numeric(candidate.get("confidence", 0))
        if not 0 <= confidence <= 1:
            raise ValueError("语音候选评分必须在0到1之间")
        item = {"key": key, "value": safe_value(candidate["value"], target), "kind": target["kind"],
                "label": target["label"], "destination": target["destination"], "source": "asr",
                "confidence": confidence, "confirmed": False, "requires_confirmation": True,
                "raw": raw, "note": note}
        for prop in ("extra_index", "extra_prop", "unit"):
            if prop in target:
                item[prop] = target[prop]
        result_candidates[key] = item  # Latest candidate replaces a duplicate field.
        if key not in result_keys:
            result_keys.append(key)
    return result_keys, list(result_candidates.values())


def normalize(body):
    if not isinstance(body, dict):
        raise ValueError("表单数据必须是对象")
    data = blank_data()
    data["is_example"] = body.get("is_example") is True
    data["additional_failure_confirmed"] = body.get("additional_failure_confirmed") is True
    for key in HEADER_TEXT:
        if key in body:
            data[key] = str(body[key] or "").strip()[:160]
    for key in NUMBER_KEYS:
        if key in body:
            value = body[key]
            data[key] = None if value is None or value == "" else numeric(value)
    data["inspection_mode"] = body.get("inspection_mode", data["inspection_mode"])
    if data["inspection_mode"] not in ("full", "sampling"):
        raise ValueError("检验方式只能是全检或抽检")
    values = body.get("values") or {}
    if not isinstance(values, dict):
        raise ValueError("检测值必须是字段对象")
    data["values"] = {}
    for key in FIELD_KEYS:
        meta = values.get(key)
        if not isinstance(meta, dict) or meta.get("value") in (None, ""):
            continue
        value = meta["value"]
        if key in ("thickness", "width"):
            if isinstance(value, str):
                value = [v for v in re.split(r"[，,、;；\s]+", value.strip()) if v]
            if not isinstance(value, list):
                value = [value]
            if len(value) > 12:
                raise ValueError("每项最多记录12个读数，请分记录填写")
            value = [numeric(v) for v in value]
        else:
            if value not in ("合格", "不合格", "不适用") or (value == "不适用" and key != "coil"):
                raise ValueError("检验结论无效: %s" % key)
        confidence = meta.get("confidence", 1 if meta.get("source", "manual") == "manual" else 0)
        confidence = numeric(confidence)
        data["values"][key] = {"value": value, "source": "asr" if meta.get("source") == "asr" else "manual",
                               "confidence": max(0, min(1, confidence)), "confirmed": meta.get("confirmed") is True,
                               "raw": str(meta.get("raw") or "")[:600]}
    extras = body.get("extra_rows") or []
    if not isinstance(extras, list) or len(extras) > 2:
        raise ValueError("原表预留两个补充尺寸行")
    for row in extras:
        if not isinstance(row, dict):
            raise ValueError("补充尺寸格式错误")
    data["extra_rows"] = [{k: str(r.get(k) or "").strip()[:80] for k in ("name", "requirement", "result", "verdict")} for r in extras]
    evidence = body.get("evidence") or []
    if not isinstance(evidence, list):
        raise ValueError("口报留痕格式错误")
    data["evidence"] = [{"text": str(e.get("text") or "")[:800], "audio_id": str(e.get("audio_id") or "")[:80],
                         "at": str(e.get("at") or "")[:40]} for e in evidence[-30:] if isinstance(e, dict)]
    data["voice_pending"], data["voice_candidates"] = _voice_review(body)
    return data


def recommended_sample(quantity):
    n = int(quantity)
    if n < 1:
        return 0
    if n < 5:
        return 1
    sample = max(5, math.ceil(n * (.10 if n <= 50 else .08)))
    return min(n, sample if n <= 50 else min(30, sample))


def schema(data=None):
    data = data or blank_data()
    checks = [{"key": key, "label": label, "syn": label, "type": "enum",
               "options": ["合格", "不合格"] + (["不适用"] if key == "coil" else []), "pass": ["合格"]} for key, label, _ in CHECKS]
    dimensions = []
    for key, label, syn in (("thickness", "钢板厚度", "厚度/板厚"), ("width", "钢板宽度", "宽度/板宽")):
        dimensions.append({"key": key, "label": label, "syn": syn, "unit": "mm"})
    return {"code": "STEEL-IQC", "revision": RECORD_CODE, "sections": [{"name": "资料与外观", "fields": checks}, {"name": "尺寸", "fields": dimensions}]}


def evaluate(data):
    issues, rows = [], []
    pending, _ = _voice_review(data)
    if pending:
        import steel_voice
        for key in pending:
            issues.append({"key": key, "message": steel_voice.TARGETS[key]["label"] + "：语音候选尚未人工核对确认"})
    labels = dict((k, label) for k, label, _ in CHECKS)
    labels.update(thickness="厚度", width="宽度")
    for key in ("document_no", "product_name", "specification", "manufacturer", "grade", "inspection_date"):
        if not data.get(key):
            issues.append({"key": key, "message": {"document_no": "单据号", "product_name": "产品名称", "specification": "规格型号",
                                                    "manufacturer": "生产单位", "grade": "牌号", "inspection_date": "检验日期"}[key] + "未填写"})
    if data.get("inspection_date"):
        try:
            datetime.strptime(data["inspection_date"], "%Y-%m-%d")
        except ValueError:
            issues.append({"key": "inspection_date", "message": "检验日期必须是有效日期"})
    spec = re.search(r"(\d+(?:\.\d+)?)\s*[*xX×]\s*(\d+(?:\.\d+)?)", data.get("specification", ""))
    if spec:
        for key, expected in (("thickness_nominal", float(spec[1])), ("width_nominal", float(spec[2]))):
            if data.get(key) is not None and not math.isclose(data[key], expected, rel_tol=0, abs_tol=1e-8):
                issues.append({"key": key, "message": "规格型号与公称尺寸不一致，请核对本批标准要求"})
    for key in ("supply_quantity", "inspected_quantity", "qualified_quantity", "rejected_quantity"):
        v = data.get(key)
        if v is None or v != int(v) or v < (1 if key in ("supply_quantity", "inspected_quantity") else 0):
            issues.append({"key": key, "message": "供货/检验数量应为正整数，合格/不合格数量应为非负整数"})
    q, n, good, bad = (data.get(k) for k in ("supply_quantity", "inspected_quantity", "qualified_quantity", "rejected_quantity"))
    if all(v is not None for v in (q, n, good, bad)):
        if n > q or good + bad != n:
            issues.append({"key": "inspected_quantity", "message": "检验数不能超过供货数，合格数与不合格数之和须等于检验数"})
        if data["inspection_mode"] == "full" and n != q:
            issues.append({"key": "inspection_mode", "message": "100%检验的检验数量必须等于交检数量"})
        if data["inspection_mode"] == "sampling" and n < recommended_sample(q):
            issues.append({"key": "inspected_quantity", "message": "抽检数不足本表备注的最低抽样数"})
    if data["inspection_mode"] == "sampling" and not data.get("sampling_basis"):
        issues.append({"key": "sampling_basis", "message": "抽样依据未填写"})
    for key in [x[0] for x in CHECKS] + ["thickness", "width"]:
        meta = data["values"].get(key) or {}
        v = meta.get("value")
        status, note = "pass", ""
        if v is None or v == []:
            status, note = "missing", "未检测或未填写"
        elif key in ("thickness", "width"):
            nominal, lower, upper = (data.get(key + suffix) for suffix in ("_nominal", "_lower", "_upper"))
            if None in (nominal, lower, upper) or nominal <= 0 or lower > upper:
                status, note = "missing", "标准要求未填写或上下偏差无效"
            elif any(x <= 0 for x in v):
                status, note = "fail", "测量尺寸必须大于0"
            elif any(x < nominal + lower - 1e-9 or x > nominal + upper + 1e-9 for x in v):
                status, note = "fail", "超出 %g～%g mm" % (nominal + lower, nominal + upper)
            else:
                note = "%g～%g mm" % (nominal + lower, nominal + upper)
        elif v == "不合格":
            status, note = "fail", "质量工程师记录不合格"
        elif v == "不适用" and (key != "coil" or "卷" in data["product_name"] or "带" in data["product_name"]):
            status, note = "missing", "钢卷/钢带的成卷与塔高检验不能标记不适用"
        if status not in ("missing",) and meta.get("source") == "asr" and not meta.get("confirmed"):
            status, note = "pending", "口报读数或结论尚未人工确认"
        rows.append({"key": key, "label": labels[key], "status": status, "value": v, "note": note})
        if status in ("missing", "pending"):
            issues.append({"key": key, "message": labels[key] + "：" + note})
    for i, row in enumerate(data.get("extra_rows") or []):
        if any(row.values()) and (not all(row.get(k) for k in ("name", "requirement", "result")) or row.get("verdict") not in ("合格", "不合格")):
            issues.append({"key": "extra_rows", "message": "补充尺寸第%d行的名称、要求、结果及人工判定需填齐" % (i + 1)})
    failed = any(r["status"] == "fail" for r in rows) or (bad is not None and bad > 0) or any(r.get("verdict") == "不合格" for r in data.get("extra_rows", []))
    if failed and bad == 0:
        issues.append({"key": "rejected_quantity", "message": "有不合格项，不能将不合格数量填写为0"})
    if failed and data["inspection_mode"] == "sampling" and data.get("disposition") not in DISPOSITIONS:
        issues.append({"key": "disposition", "message": "抽检发现不合格，须记录加倍抽检/全检/拒收/评审后处理去向"})
    if failed and data["inspection_mode"] == "sampling" and data.get("disposition") in DISPOSITIONS[1:]:
        if not data.get("additional_failure_confirmed"):
            issues.append({"key": "disposition", "message": "按原表备注，转全检/拒收/评审前须确认追加检验仍有不合格；未追加请记录加倍抽检"})
        if q is not None and n is not None and n < min(q, 2 * recommended_sample(q)):
            issues.append({"key": "inspected_quantity", "message": "实际检验数不足加倍抽检数量，请核对追加检验记录"})
    if not failed and data.get("disposition"):
        issues.append({"key": "disposition", "message": "未记录不合格品，处理去向应留空；请核对检验结果和数量"})
    return {"rows": rows, "issues": issues, "can_finalize": not issues,
            "overall": "pending" if issues else ("fail" if failed else "pass"),
            "recommended_sample": recommended_sample(q) if q is not None and q >= 1 else None,
            "thickness_range": [data.get("thickness_nominal", 0) + data.get("thickness_lower", 0), data.get("thickness_nominal", 0) + data.get("thickness_upper", 0)]
            if all(data.get(k) is not None for k in ("thickness_nominal", "thickness_lower", "thickness_upper")) else None}


def snapshot(data, template_hash):
    return hashlib.sha256(json.dumps({"data": data, "template": template_hash}, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()[:20]


def pretty(value):
    return "" if value is None else format(value, ".15g")


def check_text(meta):
    value = (meta or {}).get("value")
    if value == "不适用":
        return "不适用（非钢卷/钢带）"
    return "合格%s     不合格%s" % ("☑" if value == "合格" else "□", "☑" if value == "不合格" else "□")


def unconfirmed_asr_readings(data):
    # Older drafts stored pending readings inline instead of in voice_candidates.
    return {key for key, meta in data["values"].items() if meta.get("source") == "asr" and not meta.get("confirmed")}


def cell_values(record):
    data = record["data"]
    mode = data["inspection_mode"]
    sample = "本批量交检 %s 件，按 %s 进行抽验，抽检 %s 件，抽检结果 %s，判定本批量 %s。" % (
        pretty(data["supply_quantity"]), data["sampling_basis"], pretty(data["inspected_quantity"]),
        sampling_result(data), data.get("disposition") or ("不合格" if data["rejected_quantity"] else "合格"))
    full = "本批量交检 %s 件，进行 100%%检验，其中合格 %s 件，不合格 %s 件。" % (
        pretty(data["supply_quantity"]), pretty(data["qualified_quantity"]), pretty(data["rejected_quantity"]))
    if record["status"] != "final":
        supply, inspected, qualified, rejected = (pretty(data.get(key)) or "____" for key in
            ("supply_quantity", "inspected_quantity", "qualified_quantity", "rejected_quantity"))
        counts = "其中合格 %s 件，不合格 %s 件。" % (qualified, rejected)
        sample = "本批量交检 %s 件，按 %s 进行抽验，抽检 %s 件，%s抽检结果____，判定本批量____。" % (
            supply, data.get("sampling_basis") or "____", inspected, counts)
        full = "本批量交检 %s 件，进行 100%%检验，实际检验 %s 件，%s" % (supply, inspected, counts)
    nominal, lo, hi = (data.get(k) for k in ("thickness_nominal", "thickness_lower", "thickness_upper"))
    thickness_req = "" if None in (nominal, lo, hi) else ("%s±%s" % (pretty(nominal), pretty(hi)) if abs(lo + hi) < 1e-9 else "%s (%s/%s)" % (pretty(nominal), pretty(lo), pretty(hi)))
    # The first conclusion blank always holds the complete inspection verdict.
    # Compute it from every result rather than trusting only the quantity fields
    # or a cached evaluation supplied by a caller.
    verdict = ""
    if record["status"] == "final":
        overall = evaluate(data)["overall"]
        if overall not in ("pass", "fail"):
            raise ValueError("检测结果尚未齐全，不能生成正式结论")
        verdict = "合格" if overall == "pass" else "不合格"
    pending_readings = unconfirmed_asr_readings(data)
    values = {key: meta for key, meta in data["values"].items() if key not in pending_readings}
    cells = {"B3": data["document_no"], "D3": data["product_name"], "H3": data["specification"],
             "B4": data["manufacturer"], "D4": data["supply_quantity"], "H4": data["grade"],
             "G6": "\n".join(check_text(values.get(k)) for k, _, _ in VERIFY),
             "G7": "\n".join(check_text(values.get(k)) for k, _, _ in APPEARANCE),
             "C9": thickness_req, "B13": verdict, "F13": sample if mode == "sampling" else full,
             "B15": record.get("inspector_name") if record["status"] == "final" else "", "G15": data["inspection_date"].replace("-", ".")}
    if record["status"] != "final":
        cells["A15"] = INSPECTOR_LABEL
        cells["B14"] = SAMPLING_NOTE
    elif "sampling_note" in data:
        cells["B14"] = data["sampling_note"]
    if record["status"] == "final" and "inspector_label" in data:
        cells["A15"] = data["inspector_label"]
    # A final predating the saved note uses the text in its bound template.
    for key, address in (("thickness", "G9"), ("width", "G10")):
        readings = (values.get(key) or {}).get("value") or []
        cells[address] = readings[0] if len(readings) == 1 else "、".join(pretty(v) for v in readings)
    for i in range(2):
        row = (data.get("extra_rows") or [])
        extra = row[i] if len(row) > i else {}
        cells.update({"B%d" % (11 + i): extra.get("name", ""), "C%d" % (11 + i): extra.get("requirement", ""), "G%d" % (11 + i): extra.get("result", "")})
    return cells


def sampling_result(data):
    if not data.get("rejected_quantity"):
        return "合格"
    return "不合格（追加仍不合格）" if data.get("additional_failure_confirmed") else "不合格"
