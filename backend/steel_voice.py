# -*- coding: utf-8 -*-
"""Typed, review-only speech candidates for the editable steel form.

The recognizer receives labels/enumerations only. Dates, identifiers and free
text never pass through the generic measurement-number extractor. No candidate
signs, finalizes, or mutates a record; the UI must let the inspector review it.
"""
import copy
from datetime import date
import math
import re
import unicodedata

import cnnum
import steel
import asr_verdict
import steel_standards


def _target(key, label, kind, aliases=(), destination="header", **extra):
    return dict(key=key, label=label, kind=kind, destination=destination,
                aliases=list(aliases), **extra)


_TARGETS = [
    _target("document_no", "单据号", "identifier", ("单据编号", "单号"), placeholder="逐位报数字，前导零会保留"),
    _target("product_name", "产品名称", "text", ("产品名", "产品", "品名")),
    _target("specification", "规格型号", "text", ("规格", "型号"), placeholder="例如：五点七五乘一千八百"),
    _target("manufacturer", "生产单位", "text", ("生产厂家", "制造厂家", "供货厂家", "厂家", "生产厂", "制造商")),
    _target("supply_quantity", "供货数量", "number", ("批次数量", "供货数", "总数量"), integer=True),
    _target("grade", "牌号", "identifier", ("钢材牌号", "钢板牌号", "材质牌号"), placeholder="例如：Q三五五B"),
    _target("inspection_date", "检验日期", "date", ("检测日期", "日期"), placeholder="请报完整年月日"),
]
for _dimension, _label in (("thickness", "厚度"), ("width", "宽度")):
    _TARGETS += [
        _target(_dimension + "_nominal", _label + "公称值", "number",
                (_label + "公称尺寸", "公称" + _label, _label + "标准值"), unit="mm", positive=True),
        _target(_dimension + "_lower", _label + "下偏差", "number",
                (_label + "下公差", _label + "负偏差"), unit="mm"),
        _target(_dimension + "_upper", _label + "上偏差", "number",
                (_label + "上公差", _label + "正偏差"), unit="mm"),
    ]
_TARGETS += [
    _target("inspection_mode", "检验方式", "enum", ("检测方式", "检验模式"),
            options=["full", "sampling"], option_labels={"full": "全检", "sampling": "抽检"}),
    _target("inspected_quantity", "检验数量", "number", ("检测数量", "实检数量", "抽检数量"), integer=True),
    _target("qualified_quantity", "合格数量", "number", ("合格数",), integer=True),
    _target("rejected_quantity", "不合格数量", "number", ("不合格数",), integer=True),
    _target("sampling_basis", "抽样依据", "text", ("抽检依据", "抽样规则")),
    _target("disposition", "处理去向", "enum", ("处理方式", "处理意见", "不合格品处置"), options=list(steel.DISPOSITIONS)),
    _target("additional_failure_confirmed", "追加检验仍不合格确认", "boolean",
            ("追加确认", "追加检验确认"), options=[True, False],
            option_labels={"true": "已确认追加检验仍不合格", "false": "未确认追加检验仍不合格"},
            placeholder="追加检验仍不合格 / 尚未确认"),
]
for _key, _label, _req in steel.CHECKS:
    _TARGETS.append(_target(_key, _label, "enum", destination="value",
                            options=["合格", "不合格"] + (["不适用"] if _key == "coil" else [])))
_TARGETS += [
    _target("thickness", "钢板厚度", "number", ("厚度", "板厚"), destination="value", unit="mm", positive=True),
    _target("width", "钢板宽度", "number", ("宽度", "板宽"), destination="value", unit="mm", positive=True),
]
for _index in range(2):
    for _prop, _label in (("name", "名称"), ("requirement", "要求"), ("result", "结果"), ("verdict", "判定")):
        _n, _cn = str(_index + 1), "一二"[_index]
        _aliases = ["追加项目" + _cn + _label, "补充项目" + _n + _label, "补充项目" + _cn + _label]
        if _prop == "result":
            _aliases += ["追加项目" + _n + "实测", "追加项目" + _cn + "实测"]
        _TARGETS.append(_target("extra_%d_%s" % (_index, _prop), "追加项目" + _n + _label,
                                "enum" if _prop == "verdict" else "text", _aliases, destination="extra",
                                extra_index=_index, extra_prop=_prop,
                                **({"options": ["合格", "不合格"]} if _prop == "verdict" else {})))

TARGETS = {target["key"]: target for target in _TARGETS}
MANUAL_ONLY_KEYS = {"inspection_mode", "inspected_quantity", "qualified_quantity", "rejected_quantity",
                    "sampling_basis", "disposition", "additional_failure_confirmed"} | {
                        key for key, _label, _requirement in steel.CHECKS}
VOICE_TARGETS = {key: target for key, target in TARGETS.items()
                 if key not in MANUAL_ONLY_KEYS and key not in steel_standards.LOCKED_VOICE}
_LABELS = sorted([(name, target) for target in _TARGETS
                  for name in [target["label"]] + target["aliases"]], key=lambda item: -len(item[0]))
_PREFIX = re.compile(r"^(?:(?:请(?:填写|记录|输入)?|填入|填写|输入|记录|现在报|报一下)\s*)+")
_CONNECTOR = re.compile(r"^(?:(?:[:：=]|的|是|为|读数(?:是|为)?|结果(?:是|为)?|判定(?:是|为)?)\s*)+")
_UNCERTAIN = re.compile(r"不确定|待确认|不能确认|可能|疑似|大概|差不多|大约|左右|口误|改成|更正|不是|不要|不填|取消")
_IDENTITY = re.compile(r"^(?:质量工程师|检验员|检验人|检测员|签名|签字|验收人|审核人|复核人)")
_DIGITS = "".join(cnnum.CN_DIGITS)
_NUMBER = re.compile("^(" + cnnum.NUM_RE + ")(毫米|公厘|厘米|公分|米|mm|cm|m|件|卷|张|块|个)?$", re.I)


def voice_targets():
    """Public editable-field catalog; excludes signatures and fixed requirements."""
    return [{k: copy.deepcopy(v) for k, v in target.items() if k != "aliases"}
            for target in VOICE_TARGETS.values()]


def review_targets():
    """Keep old saved candidates reviewable without reopening voice entry."""
    return [{k: copy.deepcopy(v) for k, v in target.items() if k != "aliases"} for target in _TARGETS]


def schema():
    """Processing schema: no supplied nominal values, tolerances or form answers."""
    fields = []
    for target in VOICE_TARGETS.values():
        kind = target["kind"]
        field = {"key": target["key"], "label": target["label"], "syn": "/".join(target["aliases"]),
                 "type": "number" if kind == "number" else "enum" if kind in ("enum", "boolean") else "text"}
        if kind == "enum":
            field["options"] = list((target.get("option_labels") or {}).values()) or target["options"][:]
        elif kind == "boolean":
            field["options"] = list(target["option_labels"].values())
        if target.get("unit"):
            field["unit"] = target["unit"]
        fields.append(field)
    return {"code": "STEEL-IQC-VOICE", "revision": steel.RECORD_CODE,
            "sections": [{"name": "钢板可填写字段", "fields": fields}]}


def _normalized(text):
    text = unicodedata.normalize("NFKC", str(text or ""))[:2000]
    return re.sub(r"\s+", " ", text).strip(" ，,。；;\n\t")


def _label(text):
    for name, target in _LABELS:
        if text.startswith(name):
            return target, name
    return None, ""


def _payload(text, target):
    text = _PREFIX.sub("", text).strip()
    hit, label = _label(text)
    if hit and hit["key"] != target["key"]:
        # Extra free-text names/requirements may themselves be a field name.
        if target["destination"] != "extra" or target["kind"] != "text":
            return None
        label = ""
    if label:
        text = text[len(label):].lstrip()
    elif _IDENTITY.match(text) or re.match(r"^[^:：]{1,20}[:：]", text):
        return None
    if target["kind"] == "boolean" and text in ("是", "是的"):
        return text
    if not label and target["kind"] == "text" and target["key"] != "specification":
        # Free-text proper names can begin with 是/为/的. Only remove a spoken
        # connector after an actual field label, never from an unlabelled name.
        return text.strip(" ，,。；;\n\t")
    return _CONNECTOR.sub("", text).strip(" ，,。；;\n\t")


def _identifier(text, grade=False):
    text = re.sub(r"\s+", "", text).replace("横杠", "-").replace("杠", "-")
    if grade:
        text = text.replace("小写", "").replace("大写", "")
    # Digit-by-digit conversion preserves leading zeros in document numbers.
    text = "".join(str(cnnum.CN_DIGITS[c]) if c in cnnum.CN_DIGITS else c for c in text)
    if len(text) <= 160 and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9./_-]*", text) and re.search(r"\d", text):
        return text.upper() if grade else text
    return None


def _date(text):
    text = re.sub(r"\s+", "", text)
    part = r"([0-9" + _DIGITS + "十拾百佰千仟]+)"
    match = re.fullmatch(part + "年" + part + "月" + part + "[日号]?", text)
    if not match:
        match = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if not match:
        return None
    # Calendar years are not measured-size shorthand: keep their written
    # value instead of applying the “一千一 -> 1100” dictation convention.
    values = [cnnum.cn_to_number(x, colloquial_thousands=False) for x in match.groups()]
    if any(x is None or not x.is_integer() for x in values) or values[0] < 1000:
        return None
    try:
        return date(*[int(x) for x in values]).isoformat()
    except (ValueError, OverflowError):
        return None


def _number(text, target):
    compact = re.sub(r"\s+", "", text).replace("−", "-")
    match = _NUMBER.fullmatch(compact)
    if not match:
        return None
    value = cnnum.cn_to_number(match.group(1))
    unit = (match.group(2) or "").lower()
    if value is None:
        return None
    if target.get("unit") == "mm":
        if unit in ("件", "卷", "张", "块", "个"):
            return None
        value *= {"厘米": 10, "公分": 10, "cm": 10, "米": 1000, "m": 1000}.get(unit, 1)
    elif unit and unit not in ("件", "卷", "张", "块", "个"):
        return None
    if not math.isfinite(value) or (target.get("positive") and value <= 0):
        return None
    if target.get("integer") and (not value.is_integer() or value < 0):
        return None
    return int(value) if target.get("integer") else value


def _specification(text):
    # Longer spoken connectors must precede “乘”, or “乘上” leaves a stray
    # “上” in the second operand. Every operand is still fully validated.
    parts = re.split(r"\s*(?:[*×xX]|乘以|乘上|乘)\s*", text)
    if len(parts) not in (2, 3):
        return None
    numbers = [_number(part, {"unit": "mm", "positive": True}) for part in parts]
    if any(number is None for number in numbers):
        return None
    return "*".join(format(number, ".15g") for number in numbers)


def _enum(text, target):
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"^(?:结论|判定|结果)(?:是|为)?", "", text)
    if target["key"] == "inspection_mode":
        return {"全检": "full", "全数检验": "full", "全部检验": "full", "full": "full",
                "抽检": "sampling", "抽样检验": "sampling", "sampling": "sampling"}.get(text)
    if target["key"] == "disposition":
        text = {"转为全检": "转全检", "评审后按评审结果处理": "评审后处理"}.get(text, text)
    verdict = asr_verdict.parse_verdict(text, target.get("options"))
    if verdict is not None:
        return verdict
    if text in target.get("options", []):
        return text
    # Never infer an affirmative from a negated/uncertain substring.
    return None


def _boolean(text):
    text = re.sub(r"\s+", "", text)
    if text in ("是", "是的", "确认仍不合格", "追加检验仍不合格", "已确认追加检验仍不合格", "仍不合格", "true"):
        return True
    if text in ("否", "没有", "未确认", "尚未确认", "未确认追加检验仍不合格", "追加检验未发现不合格", "false"):
        return False
    return None


def _candidate(text, target, acoustic):
    payload = _payload(text, target)
    if payload is None or not payload:
        return None
    kind = target["kind"]
    if (kind != "text" or target["key"] == "specification") and _UNCERTAIN.search(payload):
        return None
    # Text may contain inspection terminology as literal content; numeric/id
    # fields must reject all trailing labels rather than pluck the first number.
    if kind == "number":
        value = _number(payload, target)
    elif kind == "identifier":
        value = _identifier(payload, target["key"] == "grade")
    elif kind == "date":
        value = _date(payload)
    elif kind == "enum":
        value = _enum(payload, target)
    elif kind == "boolean":
        value = _boolean(payload)
    elif target["key"] == "specification":
        value = _specification(payload)
    else:
        maximum = 80 if target["destination"] == "extra" else 160
        # A second explicit field after punctuation is a separate utterance,
        # never part of a company/product name written into the current field.
        cross = [p.strip() for p in re.split(r"[，,；;\n]", payload)[1:]]
        if len(payload) > maximum or any(_label(p)[0] for p in cross) or _IDENTITY.match(payload):
            return None
        value = payload
    if value is None or value == "":
        return None
    score = .88 if kind in ("number", "enum", "boolean") else .8
    if acoustic is not None:
        score = min(score, acoustic)
    result = {"key": target["key"], "value": value, "label": target["label"], "source": "asr",
              "raw": text, "confidence": round(score, 3), "confirmed": False,
              "requires_confirmation": True, "destination": target["destination"],
              "note": "语音候选，请逐项核对后确认填入"}
    if kind == "enum":
        verdict = asr_verdict.normalize_candidate(payload, target.get("options"))
        if verdict and verdict["changed"]:
            result["note"] = verdict["note"] + "；请核对原话后确认"
    for prop in ("extra_index", "extra_prop", "unit"):
        if prop in target:
            result[prop] = target[prop]
    return result


def _segments(text):
    # Only value-field labels split an unpunctuated measurement sequence.
    names = [name for name, target in _LABELS if target["destination"] == "value"]
    pattern = re.compile("|".join(re.escape(n) for n in names))
    for piece in re.split(r"[，,、；;。\n]+", text):
        starts = [m.start() for m in pattern.finditer(piece)]
        boundaries = [0] + [pos for pos in starts[1:] if pos > 0] + [len(piece)]
        for a, b in zip(boundaries, boundaries[1:]):
            if piece[a:b].strip():
                yield piece[a:b].strip()


def parse_utterance(text, current_key=None, asr_confidence=None):
    if current_key is not None and (not isinstance(current_key, str) or current_key not in VOICE_TARGETS):
        raise ValueError("未知钢板语音字段")
    acoustic = None
    if asr_confidence is not None:
        if isinstance(asr_confidence, bool):
            raise ValueError("识别评分必须在0到1之间")
        try:
            acoustic = float(asr_confidence)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("识别评分必须在0到1之间")
        if not math.isfinite(acoustic) or not 0 <= acoustic <= 1:
            raise ValueError("识别评分必须在0到1之间")
    normalized = _normalized(text)
    if not normalized:
        return {"assignments": [], "unknown": [], "normalized": normalized}
    selected = TARGETS.get(current_key)
    assignments, unknown = [], []
    if selected and selected["destination"] != "value":
        candidate = _candidate(normalized, selected, acoustic)
        if candidate:
            assignments.append(candidate)
        else:
            unknown.append(normalized)
    else:
        for segment in _segments(normalized):
            target, _ = _label(_PREFIX.sub("", segment))
            if not target:
                target = selected
            # With a selected check/measurement, only the 9 value fields may
            # be spoken together. Headers cannot be mistaken for measurements.
            if not target or target["key"] not in VOICE_TARGETS or (selected and target["destination"] != "value"):
                unknown.append(segment)
                continue
            candidate = _candidate(segment, target, acoustic)
            if candidate:
                assignments.append(candidate)
            else:
                unknown.append(segment)
    # Never discard a detached negation/qualification and retain only a
    # later affirmative (e.g. "不，合格"). Only fully parsed utterances may
    # contribute verdicts; explicitly labelled multi-field verdicts still work.
    verdicts = [a for a in assignments if TARGETS[a["key"]]["kind"] == "enum"
                and {"合格", "不合格"}.issubset(set(TARGETS[a["key"]].get("options", [])))]
    if unknown and verdicts:
        unknown.extend(a["raw"] for a in verdicts)
        assignments = [a for a in assignments if a not in verdicts]
    # Repeated measurements are samples of one field. Conflicting repeated
    # conclusions/header values require a new utterance, never last-write-wins.
    grouped = []
    for key in dict.fromkeys(a["key"] for a in assignments):
        same = [a for a in assignments if a["key"] == key]
        first = same[0]
        if len(same) > 1:
            if key in ("thickness", "width") and len(same) <= 12:
                first["value"] = [a["value"] for a in same]
                first["raw"] = "，".join(a["raw"] for a in same)
            else:
                unknown += [a["raw"] for a in same]
                continue
        grouped.append(first)
    return {"assignments": grouped, "unknown": unknown, "normalized": normalized}
