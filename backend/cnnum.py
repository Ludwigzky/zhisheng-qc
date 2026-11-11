# -*- coding: utf-8 -*-
"""
口报文本归一与槽位抽取（ASR 之后的那一层，真实现）。

链路：ASR 文本 → 标点/口语清洗 → 中文数字与单位换算 → 标签匹配 → 字段赋值置信度
设计原则：宁可标“待确认”，也不要猜错。低置信度一律交给人复核。
"""
import re
import math
import unicodedata

CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "两": 2, "贰": 2, "三": 3, "叁": 3,
    "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7, "八": 8,
    "捌": 8, "九": 9, "玖": 9, "幺": 1,
}
CN_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000,
            "万": 10000, "亿": 100000000}

FILLERS = ["嗯", "呃", "啊", "那个", "这个", "然后", "接着", "以及", "还有", "大概",
           "差不多", "大约", "左右", "约为", "是", "为", "读数", "测了", "量了", "现在报"]

SPLIT_RE = re.compile(r"[，,、；;。\n]+")

UNIT_TABLE = {
    "毫米": ("mm", 1.0), "公厘": ("mm", 1.0), "mm": ("mm", 1.0), "MM": ("mm", 1.0),
    "厘米": ("mm", 10.0), "cm": ("mm", 10.0), "公分": ("mm", 10.0),
    "米": ("mm", 1000.0), "m": ("mm", 1000.0),
    "丝": ("mm", 0.01), "道": ("mm", 0.01),
    "微米": ("μm", 1.0), "μm": ("μm", 1.0), "um": ("μm", 1.0),
    "千克": ("kg", 1.0), "公斤": ("kg", 1.0), "kg": ("kg", 1.0), "吨": ("kg", 1000.0),
    "度": ("°", 1.0), "兆欧": ("MΩ", 1.0), "MΩ": ("MΩ", 1.0), "欧": ("Ω", 1.0),
    "兆欧姆": ("MΩ", 1.0), "欧姆": ("Ω", 1.0), "Ω": ("Ω", 1.0),
    "千欧": ("Ω", 1000.0), "千欧姆": ("Ω", 1000.0), "kΩ": ("Ω", 1000.0),
    "毫欧": ("Ω", 0.001), "毫欧姆": ("Ω", 0.001), "mΩ": ("Ω", 0.001),
    "毫米每米": ("mm/m", 1.0), "mm/m": ("mm/m", 1.0),
    "毫米/米": ("mm/m", 1.0),
    "%": ("%", 1.0), "％": ("%", 1.0),
}

_DIGIT_CHARS = "0-9" + "".join(CN_DIGITS)
_INT_CHARS = _DIGIT_CHARS + "".join(CN_UNITS)
NUM_RE = r"[-+−负正]?(?:[%s]+(?:[.点][%s]+)?|[.点][%s]+)" % (_INT_CHARS, _DIGIT_CHARS, _DIGIT_CHARS)
_NONZERO_DIGITS = "1-9" + "".join(ch for ch, value in CN_DIGITS.items() if value)
_COLLOQUIAL_THOUSANDS = re.compile(r"([%s])[千仟]([%s])" % (_NONZERO_DIGITS, _NONZERO_DIGITS))


def cn_to_number(text, *, colloquial_thousands=True):
    """中文/阿拉伯数字 → float。

    Numeric dictation treats the complete shorthand “一千一” as 1100,
    through “九千九” as 9900. Explicit “一千零一”/“一千一十” keep
    their written place values. Dates can disable the shorthand rule;
    identifiers must use their own digit-by-digit conversion.
    """
    if text is None:
        return None
    s = re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(text))).replace("−", "-")
    sign = -1 if s.startswith(("负", "-")) else 1
    if s.startswith(("负", "-", "+", "正")):
        s = s[1:]
    s = s.replace("点", ".")
    if not s or s.count(".") > 1:
        return None
    int_part, dot, dec_part = s.partition(".")
    if dot and (not dec_part or any(c not in CN_DIGITS and c not in "0123456789" for c in dec_part)):
        return None
    if any(c not in CN_DIGITS and c not in CN_UNITS and c not in "0123456789" for c in int_part):
        return None
    def digits(chars):
        return "".join(str(CN_DIGITS[c]) if c in CN_DIGITS else c for c in chars)
    shorthand = _COLLOQUIAL_THOUSANDS.fullmatch(int_part) if colloquial_thousands else None
    if shorthand:
        total = int(digits(shorthand[1])) * 1000 + int(digits(shorthand[2])) * 100
    elif not any(c in CN_UNITS for c in int_part):
        total = int(digits(int_part) or "0")
    else:
        total, section, number = 0, 0, ""
        for ch in int_part:
            if ch in CN_DIGITS or ch in "0123456789":
                number += digits(ch)
                continue
            unit = CN_UNITS[ch]
            n = int(number) if number else 1
            if unit < 10000:
                section += n * unit
            elif unit == 10000:
                total += (section + (int(number) if number else 0)) * unit
                section = 0
            else:
                total = (total + section + (int(number) if number else 0)) * unit
                section = 0
            number = ""
        total += section + (int(number) if number else 0)
    try:
        value = float(str(total) + ("." + digits(dec_part) if dot else "")) * sign
        return value if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None


def clean_text(text):
    s = unicodedata.normalize("NFKC", str(text or ""))
    # 中文 ASR 常将工程标签 L1/L2 写成“l 一/l 二”。只规范带明确 L
    # 标记的字段序号，不根据公差或期望值增删测量数字。
    s = re.sub(r"对角线\s*[lL]\s*([一二12])", lambda m: "对角线L" +
               {"一": "1", "二": "2"}.get(m.group(1), m.group(1)), s)
    s = s.replace("，", ",").replace("。", "。")
    for f in FILLERS:
        s = re.sub(r"(?<!不)是", " ", s) if f == "是" else s.replace(f, " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _norm_unit(u):
    if not u:
        return None, None
    key = u.strip()
    if key in UNIT_TABLE:
        return UNIT_TABLE[key]
    low = key.lower()
    for k, v in UNIT_TABLE.items():
        if "Ω" not in k and k.lower() == low:
            return v
    return None, None


def _convert(value, from_unit, to_unit):
    """单位换算，返回 (值, 说明)。只做少量常见换算。"""
    if not from_unit or not to_unit or from_unit == to_unit:
        return value, None
    if from_unit == "mm" and to_unit == "μm":
        return value * 1000.0, "单位换算 mm→μm"
    if from_unit == "μm" and to_unit == "mm":
        return value / 1000.0, "单位换算 μm→mm"
    if from_unit == "kg" and to_unit == "t":
        return value / 1000.0, "单位换算 kg→t"
    if from_unit == "Ω" and to_unit == "MΩ":
        return value / 1000000.0, "单位换算 Ω→MΩ"
    if from_unit == "MΩ" and to_unit == "Ω":
        return value * 1000000.0, "单位换算 MΩ→Ω"
    return value, "单位不一致（口报 %s / 表单 %s），按原值记录" % (from_unit, to_unit)


def _base_name(name):
    """「对角线L1」→「对角线」，「对角线二」→「对角线」。返回 None 表示不是这种带序号的名字。"""
    b = re.sub(r"\s*[Ll]\s*\d+$", "", name).strip()
    if b != name and len(b) >= 2:
        return b
    b2 = re.sub(r"[一二]$", "", name).strip()
    if b2 != name and len(b2) >= 2:
        return b2
    return None


def _field_index(fields):
    """
    别名索引。顺序即优先级：
      1) 显式别名（长优先）——「对角线L1」
      2) 带序号尾的别名——「对角线一」，只在没有别的匹配时才用，且不许做包含匹配
      3) 去掉序号的基础名——「对角线」，命中后靠当前高亮字段消歧
    """
    explicit, numbered, bases = [], [], []
    for f in fields:
        names = [f.get("label", "")]
        names += [x for x in str(f.get("syn", "")).replace("／", "/").replace("，", "/").split("/") if x]
        for n in names:
            n = n.strip()
            if not n:
                continue
            base = _base_name(n)
            if base:
                bases.append((base, f))
            # 带 Latin/数字序号（对角线L1 / 对角线1）能明确区分，算精确别名
            if re.search(r"[Ll]\s*\d$", n) or re.search(r"\s\d$", n):
                explicit.append((n, f))
            elif re.search(r"[一二]$", n) and base:
                numbered.append((n, f))      # 「对角线二」这类中文序号容易吃掉数字
            elif base:
                numbered.append((n, f))
            else:
                explicit.append((n, f))
    explicit.sort(key=lambda x: -len(x[0]))
    numbered.sort(key=lambda x: -len(x[0]))
    seen = set()
    out = []
    for name, f in explicit:
        if (name, f["key"]) not in seen:
            seen.add((name, f["key"]))
            out.append((name, f, "explicit"))
    for name, f in numbered:
        if (name, f["key"]) not in seen:
            seen.add((name, f["key"]))
            out.append((name, f, "numbered"))
    base_owner = {}
    for name, f in bases:
        base_owner.setdefault(name, []).append(f["key"])
    for name, keys in base_owner.items():
        for f in fields:
            if f["key"] == keys[0]:
                out.append((name, f, "base"))
                break
    return out, base_owner


def _is_cjk(ch):
    return u"\u4e00" <= ch <= u"\u9fff"


def _match_label(seg, fields, idx, base_owner=None, prefer=None):
    """返回 (field, confidence, note, matched_name)。四级：前缀 → 包含 → 基础名 → 模糊。"""
    s = seg.strip()
    # 1) 显式别名前缀（带 L1/1 这类序号的，后面直接跟数字也认）
    for name, f, kind in idx:
        if kind != "explicit" or len(name) < 2:
            continue
        if s.startswith(name):
            rest = s[len(name):].strip()
            specific = bool(re.search(r"[Ll]\s*\d$|\d$", name))
            if f.get("type") == "enum" or specific or rest == "" or not _is_cjk(rest[0]):
                return f, 0.95, "标签精确匹配", name
    # 2) 显式别名包含（跳过带序号尾的名字，避免「对角线二」吃掉数字）
    for name, f, kind in idx:
        if kind != "explicit" or len(name) < 2:
            continue
        if name in s:
            return f, 0.88, "标签包含匹配：%s" % name, name
    for name, f, kind in idx:
        if kind == "numbered" and s.startswith(name):
            rest = s[len(name):]
            if rest and (rest[0].isspace() or rest[0].isdigit() or rest[0] in ":："):
                return f, 0.90, "带序号标签匹配", name
    # 3) 基础名（去掉序号）→ 用当前高亮字段消歧
    for name, f, kind in idx:
        if kind != "base":
            continue
        if s.startswith(name):
            keys = (base_owner or {}).get(name, [])
            if prefer and prefer in keys:
                hit = next((x for x in fields if x["key"] == prefer), f)
                return hit, 0.80, "标签「%s」有歧义，按当前字段取 %s" % (name, hit.get("label")), name
            note = "标签「%s」有歧义，取第一个相近字段" % name
            return f, 0.62, note, name
    # 4) 模糊
    m = re.match(r"^([^\d\-+−零〇一二三四五六七八九十百千万点两]{2,14})", s)
    head = (m.group(1) if m else s).strip("的:： ")
    if not head:
        return None, 0.0, "", ""
    best = None
    for name, f, kind in idx:
        if len(name) >= 2 and (name.startswith(head) or head.startswith(name)):
            score = min(len(name), len(head))
            if best is None or score > best[0]:
                best = (score, f, name)
    if best:
        return best[1], 0.55, "标签模糊匹配：%s → %s" % (head, best[2]), ""
    return None, 0.0, "", ""


def _match_enum(seg, f):
    options = f.get("options") or []
    # 否定/不确定表达先处理，不能把“不可靠”中的“可靠”识别为合格。
    if re.search(r"不确定|待确认|未确认|未检|没检|不能确认|不太|不是|可能|疑似", seg):
        return None, 0.0, "枚举表达有否定或不确定语义，请重报"
    for negative in ("不合格", "超差", "不齐全"):
        if negative in seg:
            # 不把泛称“不合格”猜成“咬边”等具体缺陷；保留口报原意并要求确认。
            return negative, 0.70, "否定口报保留原意，请核对具体结论"
    hits, occupied = [], []
    for opt in sorted(options, key=len, reverse=True):
        for match in re.finditer(re.escape(opt), seg):
            if any(a <= match.start() < b for a, b in occupied):
                continue
            if re.search(r"(?:不|未|无|没有|未见|没见)$", seg[:match.start()].rstrip()):
                return None, 0.0, "枚举项被否定，请重报明确结论"
            hits.append(opt)
            occupied.append(match.span())
    hits = list(dict.fromkeys(hits))
    if len(hits) == 1:
        return hits[0], 0.92, "枚举命中"
    if len(hits) > 1:
        return None, 0.0, "同一句包含多个结论，请重报"
    if "合格" in seg:
        ok = (f.get("pass") or [None])[0]
        if ok:
            return ok, 0.90, "口报“合格”"
    return None, 0.0, ""


def _join_spoken_digits(text):
    """仅合并逐位读数中的空格；两个完整阿拉伯读数仍然保持分开。"""
    text = re.sub(r"([负正+−-])\s+(?=[%s.点])" % _DIGIT_CHARS, r"\1", text)
    chars = _INT_CHARS + ".点负正+−-"
    def join(match):
        parts = match.group(0).split()
        if len(parts) < 2:
            return match.group(0)
        # “26 . 5”、中文位数词和每段单个数字可以组合。
        if any(p in (".", "点") for p in parts) or all(
                len(p) == 1 or all(c in CN_DIGITS or c in CN_UNITS for c in p) for p in parts):
            return "".join(parts)
        return match.group(0)
    return re.sub(r"[%s]+(?:\s+[%s]+)+" % (chars, chars), join, text)


def _segments(cleaned, idx):
    """ASR 未输出标点时，按明确的不同字段标签切句。"""
    names = sorted({name for name, _, kind in idx if kind == "explicit" and len(name) >= 2}, key=len, reverse=True)
    pattern = re.compile("|".join(re.escape(n) for n in names)) if names else None
    for raw in SPLIT_RE.split(cleaned):
        if not pattern:
            yield raw
            continue
        starts = [m.start() for m in pattern.finditer(raw)]
        boundaries = [0] + [pos for pos in starts[1:] if pos > 0] + [len(raw)]
        for a, b in zip(boundaries, boundaries[1:]):
            yield raw[a:b]


def _spoken_unit(tail):
    tail = tail.lstrip()
    for unit in sorted(UNIT_TABLE, key=len, reverse=True):
        if tail.startswith(unit) or ("Ω" not in unit and tail.lower().startswith(unit.lower())):
            normalized, factor = _norm_unit(unit)
            return normalized, factor, unit
    unknown = re.match(r"[A-Za-z\u4e00-\u9fffμΩ°%/]+", tail)
    return None, None, unknown.group(0) if unknown else ""


def parse_utterance(text, fields, current_key=None, asr_confidence=None):
    """
    返回:
      assignments: [{key,label,raw,value,unit,confidence,note,source}]
      unknown:     未能对应到字段的片段
      normalized:  清洗后的文本
    """
    cleaned = clean_text(text)
    idx, base_owner = _field_index(fields)
    by_key = {f["key"]: f for f in fields}
    assignments, unknown = [], []
    cursor = current_key
    order = [f["key"] for f in fields if not f.get("derived")]

    for raw_seg in _segments(cleaned, idx):
        seg = raw_seg.strip()
        if not seg:
            continue
        field, conf, note, matched = _match_label(seg, fields, idx, base_owner, prefer=cursor)

        # 枚举类
        if field is not None and field.get("type") == "enum":
            val, c2, n2 = _match_enum(seg, field)
            if val is not None:
                assignments.append(_mk(field, val, seg, None, min(conf, c2) or c2,
                                       "；".join(x for x in (note, n2) if x)))
                cursor = _next_key(order, field["key"], cursor)
                continue
            unknown.append(seg)
            continue

        # 数值类（先把命中的标签从片段里摘掉，避免 L1/L2 里的数字被当实测值）
        num_seg = seg
        if matched:
            num_seg = seg.replace(matched, " ", 1)
        if field is not None and field.get("type") == "text":
            value = num_seg.strip(" :：")
            if value:
                assignments.append(_mk(field, value, seg, None, conf, note))
                cursor = _next_key(order, field["key"], cursor)
            else:
                unknown.append(seg)
            continue
        num_seg = _join_spoken_digits(num_seg)
        hits = list(re.finditer(NUM_RE, num_seg))
        # “1.2千欧”中的“千”属于单位；不能把它当成第二个读数。
        if hits:
            first = hits[0]
            tail = num_seg[first.end():]
            _, _, spoken_unit = _spoken_unit(tail)
            if spoken_unit and _norm_unit(spoken_unit)[0]:
                unit_end = first.end() + len(tail) - len(tail.lstrip()) + len(spoken_unit)
                hits = [first] + [h for h in hits[1:] if h.start() >= unit_end]
        # 两个读数可能是口误更正或未识别的标签；不能擅自选择“最长”的数字。
        if len(hits) > 1:
            unknown.append(seg)
            continue
        m = hits[0] if hits else None
        if not m:
            # 可能是纯枚举口报（未带标签）
            hit_enum = None
            for f in fields:
                if f.get("type") == "enum":
                    val, c2, n2 = _match_enum(seg, f)
                    if val is not None and (cursor == f["key"]):
                        hit_enum = (f, val, c2, n2)
                        break
            if hit_enum:
                f, val, c2, n2 = hit_enum
                assignments.append(_mk(f, val, seg, None, 0.70, "未报标签，按当前字段填入"))
                cursor = _next_key(order, f["key"], cursor)
            else:
                unknown.append(seg)
            continue

        num_text = m.group(0)
        unit_norm, factor, unit_text = _spoken_unit(num_seg[m.end():])
        value = cn_to_number(num_text)
        if value is None:
            unknown.append(seg)
            continue
        if field is None:
            if cursor and cursor in by_key:
                if num_seg[:m.start()].strip(" :：="):
                    unknown.append(seg)
                    continue
                field = by_key[cursor]
                conf, note = 0.88, "未报标签，按当前高亮字段填入"
            else:
                unknown.append(seg)
                continue
        if field.get("type") == "enum":
            unknown.append(seg)
            continue

        note_extra = None
        if unit_text and not unit_norm:
            conf = min(conf, 0.55)
            note_extra = "未识别的单位或读数尾词「%s」，请核对" % unit_text
        if factor and factor != 1.0:
            value = value * factor
            note_extra = "单位换算 %s" % unit_text
        if unit_norm and field.get("unit"):
            value, conv = _convert(value, unit_norm, field.get("unit"))
            if conv:
                note_extra = conv
                if conv.startswith("单位不一致"):
                    conf = min(conf, 0.55)

        # 保留真实读数精度，不能把 2.54 四舍五入成 2.5 后误判合格。
        assignments.append(_mk(field, value, seg, unit_norm, conf,
                              "；".join([x for x in [note, note_extra] if x])))
        cursor = _next_key(order, field["key"], cursor)

    if asr_confidence is not None:
        try:
            acoustic = float(asr_confidence)
            acoustic = max(0.0, min(1.0, acoustic)) if math.isfinite(acoustic) else 0.0
        except (TypeError, ValueError):
            acoustic = 0.0
        for assignment in assignments:
            assignment["confidence"] = min(assignment["confidence"], acoustic)
    return {"assignments": assignments, "unknown": unknown, "normalized": cleaned}


def _mk(field, value, raw, unit, conf, note):
    return {
        "key": field["key"],
        "label": field.get("label", field["key"]),
        "value": value,
        "raw": raw,
        "unit": unit,
        "confidence": round(float(conf), 2),
        "note": note or "",
        "source": "asr",
        "derived": bool(field.get("derived")),
    }


def _next_key(order, key, cursor):
    try:
        i = order.index(key)
    except ValueError:
        return cursor
    return order[i + 1] if i + 1 < len(order) else None
