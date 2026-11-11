# -*- coding: utf-8 -*-
"""PDF 生成器自检：出 PDF + PNG 预览 + 结构校验 + 从 PDF 字节反解渲染。"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "backend"))

from pdf_doc import Canvas, PDF, pick_font  # noqa: E402
import verify_pdf  # noqa: E402

W, H = 595.28, 841.89
out_dir = os.path.join(HERE, "..", "_probe")
if not os.path.isdir(out_dir):
    os.makedirs(out_dir)

c = Canvas(W, H)
pdf = PDF(c, title="测试")
c.text(40, 40, "主梁过程检验记录 · 中文渲染测试", 16)
c.line(40, 68, W - 40, 68, 1.0, 0.2)
y = 84
for lab, val in [("跨度实测 L", "22501 mm"), ("对角线差 Δ", "1.2 mm"), ("主梁上拱度 F", "26.5 mm")]:
    c.text(44, y, lab, 10)
    c.text(240, y, val, 10, align="right")
    y += 20
c.rect(40, y + 8, W - 80, 40, fill=0.95, stroke=0.6)
c.text(50, y + 18, "判定：合格（依据 GB/T 3811-2008，模板 T-IPQC-02 v2.0）≥ ≤ ℃ Ω μ ×", 10)

pdf_path = os.path.join(out_dir, "test.pdf")
pdf.save(pdf_path)
with open(os.path.join(out_dir, "test_canvas.png"), "wb") as f:
    f.write(pdf.render_png())
with open(os.path.join(out_dir, "test_from_pdf.png"), "wb") as f:
    f.write(verify_pdf.render_from_pdf(pdf_path, pick_font()))

problems, stats = verify_pdf.verify(pdf_path, c, pdf)
print("pdf bytes:", os.path.getsize(pdf_path))
print("stats:", stats)
print("problems:", len(problems))
for p in problems[:20]:
    print("  -", p)

sample = "主梁过程检验记录 判定：合格 超差 缺项 待确认 ≥ ≤ ℃ Ω μ × · — ①②③ → ← ↑ ↓ 【】「」"
missing = verify_pdf.check_glyph_coverage(sample)
print("missing glyph chars:", missing)
print("OK" if not problems and not missing else "FAIL")
