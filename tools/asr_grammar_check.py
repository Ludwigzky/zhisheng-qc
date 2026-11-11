# -*- coding: utf-8 -*-
"""语法约束识别自检：SRGS 生成是否合法 + worker 的 LOAD/DICTATION 协议。"""
import os
import re
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
BACKEND = os.path.join(HERE, "..", "backend")
sys.path.insert(0, BACKEND)

os.environ["ASR_SAPI_WORKER"] = os.path.join(BACKEND, "asr_mock_worker.ps1")

import asr_engine  # noqa: E402
import asr_grammar  # noqa: E402
import seed  # noqa: E402

OUT = os.path.join(HERE, "..", "_probe")
if not os.path.isdir(OUT):
    os.makedirs(OUT)
fails = []


def check(name, cond, extra=""):
    print(("  OK  " if cond else "  BAD ") + name + (("  " + str(extra)[:160]) if extra else ""))
    if not cond:
        fails.append(name)


print("=== 1. SRGS 生成 ===")
xml = asr_grammar.build_srgs(seed.TPL_IPQC)
check("XML 可解析", True)
root = ET.fromstring(xml)
check("根元素是 grammar", root.tag.endswith("grammar"), root.tag)
check("含 root 规则", root.find(".//{*}rule[@id='root']") is not None)
check("含数字规则", root.find(".//{*}rule[@id='numf']") is not None)
for token in ("跨度", "上拱度", "水平度", "焊缝外观", "涂装膜厚", "合格", "咬边"):
    check("词表含「%s」" % token, token in xml)
for ch in ("零", "九", "万", "十", "百"):
    check("数字字符含「%s」" % ch, ("<item>%s</item>" % ch) in xml)
check("有 repeat 重复度", 'repeat="1-12"' in xml)
check("小数点/负号在结构位置", ("点" in xml) and ("负" in xml) and ("<item>点</item>" not in xml))
check("语言标记 zh-CN", 'xml:lang="zh-CN"' in xml)

# 将 SRGS 的有限规则编为匹配器，验证真实说法可达，而不只检查词表含字。
def accepts(grammar_xml, utterance):
    tree = ET.fromstring(grammar_xml)
    rules = {node.attrib["id"]: node for node in tree.findall("{*}rule")}

    def literal(s):
        return re.escape(re.sub(r"\s+", "", s or ""))

    def pattern(node):
        tag = node.tag.rsplit("}", 1)[-1]
        if tag == "ruleref":
            return pattern(rules[node.attrib["uri"][1:]])
        if tag == "one-of":
            result = "(?:" + "|".join(pattern(c) for c in node) + ")"
        else:
            result = literal(node.text) + "".join(pattern(c) + literal(c.tail) for c in node)
        repeat = node.attrib.get("repeat")
        if repeat:
            lo, _, hi = repeat.partition("-")
            result = "(?:%s){%s,%s}" % (result, lo, hi or lo)
        return result

    return re.fullmatch(pattern(rules[tree.attrib["root"]]),
                        re.sub(r"\s+", "", utterance)) is not None


for phrase in ("跨度 二二五零一 毫米", "主梁上拱度 二六点五 毫米",
               "水平度 零点三 毫米每米", "膜厚 一三五 微米", "焊缝外观 咬边",
               "跨度一二零五水平度零点三"):
    check("语法接受实际口报「%s」" % phrase, accepts(xml, phrase))
focused = asr_grammar.build_srgs(seed.TPL_IPQC, current_key="weld")
for phrase in ("合格", "咬边", "焊缝外观气孔", "不合格", "焊缝外观不合格", "焊缝外观是不合格", "未检", "不确定"):
    check("枚举焦点接受「%s」" % phrase, accepts(focused, phrase))
check("枚举焦点拒绝裸数字", not accepts(focused, "一二三"))
check("枚举焦点拒绝其他字段", not accepts(focused, "跨度一二三"))
focused_num = asr_grammar.build_srgs(seed.TPL_IPQC, current_key="camber")
check("数字焦点支持超差数值", accepts(focused_num, "上拱度一八毫米"))
check("数字焦点支持裸数字单位", accepts(focused_num, "二六点五毫米"))
check("数字焦点排除其他字段", not accepts(focused_num, "涂装膜厚一三五微米"))
try:
    asr_grammar.build_srgs(seed.TPL_IPQC, current_key="missing")
    check("未知焦点必须报错", False)
except ValueError:
    check("未知焦点必须报错", True)

print("\n=== 2. 落盘（UTF-8 BOM）===")
path, items = asr_grammar.write_grammar(seed.TPL_IPQC, OUT)
check("文件存在", os.path.exists(path), path)
check("条目数合理", 10 <= items <= 200, items)
with open(path, "rb") as f:
    head = f.read(3)
check("带 BOM", head == b"\xef\xbb\xbf", head)
with open(path, encoding="utf-8-sig") as f:
    again = f.read()
check("落盘内容与生成一致", again.strip() == xml.strip())
focus_path, _ = asr_grammar.write_grammar(seed.TPL_IPQC, OUT, current_key="camber")
check("不同焦点生成不同缓存文件", focus_path != path)

print("\n=== 3. worker 协议：LOAD / DICTATION ===")
eng = asr_engine.SapiEngine()
check("worker 就绪", eng.available is True)
r = eng.load_grammar(path)
check("加载语法成功", r.get("ok") is True, r)
check("模式切到 grammar", eng.info().get("mode") == "grammar", eng.info().get("mode"))
check("记录了语法文件名", bool(eng.info().get("grammar")), eng.info().get("grammar"))

# 语法模式下识别仍然可用
import audio  # noqa: E402
import numpy as np  # noqa: E402
t = np.arange(16000) / 16000.0
wav = os.path.join(OUT, "case_ok.wav")
audio.write_wav(wav, 16000, (0.2 * np.sin(2 * np.pi * 180 * t)).astype(np.float32))
res = eng.transcribe(wav)
check("语法模式下有识别结果", res.get("text") == "跨度 二万二千五百零一", res.get("text"))

r2 = eng.set_dictation()
check("切回听写", r2.get("ok") is True and eng.info().get("mode") == "dictation", r2)

print("\n=== 4. 语法加载失败要回落而不是崩 ===")
bad = os.path.join(OUT, "case_bad.grxml")
with open(bad, "w", encoding="utf-8") as f:
    f.write("<grammar")
r3 = eng.load_grammar(bad)
check("失败返回 ok=False", r3.get("ok") is False, r3)
check("错误原因非空", bool(r3.get("error")), r3.get("error"))
res2 = eng.transcribe(wav)
check("失败后仍能识别（已回落）", res2.get("text") == "跨度 二万二千五百零一", res2.get("text"))
eng.close()

print("\n=== 5. 空白模板也不能炸 ===")
try:
    asr_grammar.build_srgs({"sections": []})
    check("空 schema 不抛异常", True)
except Exception as exc:
    check("空 schema 不抛异常", False, exc)

print("\nFAILS:", len(fails), fails if fails else "")
print("OK" if not fails else "FAIL")
sys.exit(1 if fails else 0)
