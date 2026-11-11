# -*- coding: utf-8 -*-
"""
把检验表单编成 SRGS 语法（.grxml），喂给 Windows 识别引擎。

语法识别只搜索表单标签、数字、单位和枚举；可进一步限定当前字段。
语法不改变声学模型，真实收益需要用现场录音测量。
"""
import os
import re

# 数字口播字符集：既能吃"二二五零一"这种逐位报法，也能吃"二万二千五百零一"
# 刻意不放「亿」「点」「负」：它们只允许出现在 numf 的结构位置，
# 否则引擎会在数字中间乱插（实测出现过「亿七点二五零亿」这种串）。
NUM_CHARS = u"零〇一二三四五六七八九十百千万两"
NUM_ITEMS = "".join("<item>%s</item>" % c for c in NUM_CHARS)
DIGIT_ITEMS = "".join("<item>%s</item>" % c for c in u"零〇一二三四五六七八九两")

# 仅加入解析层已支持的单位。测量范围/公差不是识别词表，超差值必须能报。
SPOKEN_UNITS = {
    "mm": ("毫米", "公厘", "厘米", "公分", "米", "丝", "道"),
    "mm/m": ("毫米每米",),
    "μm": ("微米", "毫米"),
    "kg": ("千克", "公斤", "吨"),
    "MΩ": ("兆欧",),
    "Ω": ("欧",),
    "°": ("度",),
}

TEMPLATE = u"""<?xml version="1.0" encoding="utf-8"?>
<grammar version="1.0" xml:lang="zh-CN" root="root" mode="voice"
         xmlns="http://www.w3.org/2001/06/grammar" tag-format="semantics/1.0">
  <rule id="root" scope="public">
    <item repeat="1-12"><ruleref uri="#entry"/></item>
  </rule>
  <rule id="entry">
    <one-of>
{body}
    </one-of>
  </rule>
  <rule id="num">
    <item repeat="1-12">
      <one-of>{numitems}</one-of>
    </item>
  </rule>
  <rule id="digits">
    <item repeat="1-12"><one-of>{digititems}</one-of></item>
  </rule>
  <rule id="numf">
    <one-of>
      <item><ruleref uri="#num"/></item>
      <item><ruleref uri="#num"/>点<ruleref uri="#digits"/></item>
      <item>点<ruleref uri="#digits"/></item>
      <item>负<ruleref uri="#num"/></item>
      <item>负<ruleref uri="#num"/>点<ruleref uri="#digits"/></item>
    </one-of>
  </rule>
</grammar>
"""


def _esc(s):
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _aliases(f):
    """字段可播报的名字：标签 + 同义词，去重、去括号、限长。"""
    names = [f.get("label", "")]
    raw = str(f.get("syn", "") or "")
    names += [x for x in re.split(r"[/／，,]", raw) if x]
    out = []
    for n in names:
        n = re.sub(r"[（(].*?[)）]", "", str(n)).strip()
        # 标签常带工程符号（跨度实测 L、主梁上拱度 F）；口报无需读出符号。
        # 保留 L1/L2 序号，避免把两条对角线合并。
        plain = re.sub(r"\s+[A-Za-zΔδ]+$", "", n).strip()
        for alias in (n, plain):
            if not alias or len(alias) > 16:
                continue
            if len(alias) == 1 and not (u"\u4e00" <= alias <= u"\u9fff"):
                continue
            if alias not in out:
                out.append(alias)
    return out


def build_srgs(schema, max_items=160, current_key=None):
    """生成语法。current_key 可缩小到一个字段；省略时保留全表口报。"""
    body, seen = [], set()

    def add(line):
        if line in seen or len(body) >= max_items:
            return
        seen.add(line)
        body.append(line)

    fields = []
    for sec in schema.get("sections", []):
        fields.extend(sec.get("fields", []))
    fields.extend(schema.get("rules", []))
    if current_key is not None:
        selected = [f for f in fields if f.get("key") == current_key]
        if not selected:
            raise ValueError("当前字段不存在: %s" % current_key)
        fields = selected

    # 命令与裸值先加入，避免大模板的 max_items 截断它们。
    for w in (u"确认", u"重来"):
        add(u'<item>%s</item>' % _esc(w))

    def number_item(f):
        unit = SPOKEN_UNITS.get(f.get("unit"), ())
        tail = ('<item repeat="0-1"><one-of>%s</one-of></item>' %
                "".join("<item>%s</item>" % _esc(u) for u in unit)) if unit else ""
        return '<ruleref uri="#numf"/>' + tail

    def enum_options(f):
        # 否定与未检也必须可说，不能把“词表不允许的否定”挤成正向合格。
        return list(dict.fromkeys([o for o in (f.get("options") or []) if o] +
                                  ["合格", "不合格", "超差", "不齐全", "不确定", "待确认", "未检"]))

    for f in fields:
        if f.get("type") == "enum":
            for opt in enum_options(f):
                if opt:
                    add(u'<item>%s</item>' % _esc(opt))
        elif f.get("type") != "text":
            add(u'<item>%s</item>' % number_item(f))

    # 空模板仍允许协议基本词，兼容引擎自检。
    if not fields:
        add(u'<item><ruleref uri="#numf"/></item>')

    for f in fields:
        if f.get("type") == "text":
            continue  # 任意炉批号等自由文本不能伪装成受约束数字。
        for name in _aliases(f):
            if f.get("type") == "enum":
                opts = enum_options(f)
                if opts:
                    inner = "".join("<item>%s</item>" % _esc(o) for o in opts)
                    connector = '<item repeat="0-1"><one-of><item>是</item><item>为</item></one-of></item>'
                    add(u'<item>%s %s <one-of>%s</one-of></item>' % (_esc(name), connector, inner))
                add(u'<item>%s</item>' % _esc(name))
            else:
                connector = '<item repeat="0-1"><one-of><item>是</item><item>为</item></one-of></item>'
                add(u'<item>%s %s %s</item>' % (_esc(name), connector, number_item(f)))

    if current_key is None:
        for w in (u"合格", u"不合格"):
            add(u'<item>%s</item>' % _esc(w))

    return TEMPLATE.format(body="\n      ".join(body), numitems=NUM_ITEMS, digititems=DIGIT_ITEMS)


def write_grammar(schema, out_dir, name=None, current_key=None):
    """写出 .grxml（UTF-8 带 BOM）。文件名带内容哈希，语法一改就会重新加载。"""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    code = schema.get("code") or "form"
    rev = schema.get("revision") or "v0"
    xml = build_srgs(schema, current_key=current_key)
    import hashlib
    digest = hashlib.sha1(xml.encode("utf-8")).hexdigest()[:6]
    fname = (name or ("%s_%s_%s" % (code, rev, digest))).replace("/", "_") + ".grxml"
    path = os.path.join(out_dir, fname)
    with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
        f.write(xml)
    # 条目数只统计可选口报，不把单位/数字规则内的 item 重复算入。
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml)
    items = len(root.find("{*}rule[@id='entry']/{*}one-of"))
    return path, items
