# -*- coding: utf-8 -*-
"""按“指定规格”排版检验记录表：产品 + 多张表单合并成一份可签字归档的 PDF。"""
from pdf_doc import Canvas, PDF

W, H = 595.28, 841.89
M = 34.0
BRAND = "#0b3d66"
BRAND_SOFT = "#eef3f8"
INK = "#1f2933"
GRAY = "#6b7280"
LINE = "#c9d3de"
OK = "#12855b"
BAD = "#c0392b"
WARN = "#b45309"
NA = "#9aa4b0"

STATUS_TEXT = {"ok": "合格", "over": "超差", "pending": "待确认", "missing": "缺项", "na": "—"}
STATUS_COLOR = {"ok": OK, "over": BAD, "pending": WARN, "missing": BAD, "na": NA}
VERDICT_TEXT = {"pass": "合格", "fail": "超差", "pending": "待确认"}
CIRCLED = "①②③④⑤⑥⑦⑧⑨"

BOTTOM = H - 54          # 内容下边界


class ReportDoc(object):
    def __init__(self, title, footer_left, total_pages=None):
        self.c = Canvas(W, H)
        self.pdf = PDF(self.c, title=title)
        self.title = title
        self.footer_left = footer_left
        self.total_pages = total_pages
        self.page_no = 1
        self.y = 0.0
        self._header(first=True)

    # ---------------- 页眉页脚 ----------------
    def _header(self, first=False):
        c = self.c
        c.text(M, 28, "卫华集团", 8.4, color=GRAY)
        c.text(W - M, 28, "质量管理部 · 检验记录", 8.4, align="right", color=GRAY)
        if first:
            c.text(W / 2.0, 41, self.title, 15.5, align="center", color=BRAND)
            c.line(M, 68, W - M, 68, 1.2, color=BRAND)
            self.y = 78
        else:
            c.text(W / 2.0, 38, self.title + "（续）", 10.5, align="center", color=BRAND)
            c.line(M, 58, W - M, 58, 0.7, color=LINE)
            self.y = 68

    def _footer(self):
        c = self.c
        c.line(M, H - 36, W - M, H - 36, 0.5, color=LINE)
        left_max = (W - 2 * M) * 0.70
        c.text(M, H - 30, self._fit(self.footer_left, 6.8, left_max), 6.8, color=NA)
        right = "第 %d 页 / 共 %s 页" % (self.page_no,
                                       self.total_pages if self.total_pages else self.page_no)
        c.text(W - M, H - 30, right, 6.8, align="right", color=NA)

    def _fit(self, s, size, max_w):
        s = str(s or "")
        if self.c.text_width(s, size) <= max_w:
            return s
        while s and self.c.text_width(s + "…", size) > max_w:
            s = s[:-1]
        return s + "…"

    def new_page(self):
        self._footer()
        self.c.new_page()
        self.page_no += 1
        self._header(first=False)

    def ensure(self, need):
        if self.y + need > BOTTOM:
            self.new_page()
            return True
        return False

    # ---------------- 组件 ----------------
    def meta_block(self, items, cols=2):
        c = self.c
        rows = (len(items) + cols - 1) // cols
        h = rows * 15 + 8
        c.rect(M, self.y, W - 2 * M, h, fill="#f7f9fc", stroke=LINE, line_width=0.5)
        colw = (W - 2 * M) / cols
        for i, (k, v) in enumerate(items):
            r, col = divmod(i, cols)
            x = M + 8 + col * colw
            yy = self.y + 5 + r * 15
            c.text(x, yy, k, 8.0, color=GRAY)
            c.text(x + 72, yy, str(v), 8.4, color=INK)
        self.y += h + 10

    def section_bar(self, left, right=""):
        c = self.c
        self.ensure(40)
        c.rect(M, self.y, W - 2 * M, 19, fill=BRAND_SOFT)
        c.rect(M, self.y, 3.2, 19, fill=BRAND)
        c.text(M + 11, self.y + 5.2, left, 9.8, color=BRAND)
        if right:
            c.text(W - M - 8, self.y + 6.0, right, 8.2, align="right", color=GRAY)
        self.y += 24

    def table(self, headers, rows, weights=None, row_h=15, header_h=17):
        c = self.c
        weights = weights or [1.0] * len(headers)
        total = sum(weights)
        scale = (W - 2 * M) / total
        widths = [w * scale for w in weights]

        def draw_header():
            c.rect(M, self.y, W - 2 * M, header_h, fill=BRAND)
            x = M
            for title, w in zip(headers, widths):
                c.text(x + 5, self.y + 4.4, title, 8.2, color="#ffffff")
                x += w
            self.y += header_h

        if rows:
            self.ensure(header_h + row_h * min(3, len(rows)) + 12)
            draw_header()
        for idx, row in enumerate(rows):
            if self.y + row_h > BOTTOM:
                self.new_page()
                draw_header()
            if idx % 2 == 1:
                c.rect(M, self.y, W - 2 * M, row_h, fill="#fafcfe")
            x = M
            for i, cell in enumerate(row):
                text, color, align = cell if isinstance(cell, tuple) else (cell, INK, "left")
                if align == "right":
                    c.text(x + widths[i] - 6, self.y + 3.8, text, 8.2, align="right", color=color)
                elif align == "center":
                    c.text(x + widths[i] / 2.0, self.y + 3.8, text, 8.2, align="center", color=color)
                else:
                    c.text(x + 5, self.y + 3.8, text, 8.2, color=color)
                x += widths[i]
            c.line(M, self.y + row_h, W - M, self.y + row_h, 0.35, color=LINE)
            self.y += row_h
        c.line(M, self.y, W - M, self.y, 0.5, color=LINE)
        self.y += 7

    def callout(self, text, sub="", color=OK, height=40):
        c = self.c
        self.ensure(height + 12)
        c.rect(M, self.y, W - 2 * M, height, fill="#f7f9fc", stroke=LINE, line_width=0.5)
        c.rect(M, self.y, 4, height, fill=color)
        c.text(M + 13, self.y + 7, text, 11.8, color=color)
        if sub:
            c.text(M + 13, self.y + 23, sub, 8.2, color=GRAY)
        self.y += height + 12

    def sign_block(self, signers):
        c = self.c
        self.ensure(60)
        c.text(M, self.y, "签字确认", 8.8, color=BRAND)
        self.y += 13
        n = len(signers)
        gap = 10
        bw = (W - 2 * M - gap * (n - 1)) / n
        for i, s in enumerate(signers):
            x = M + i * (bw + gap)
            c.rect(x, self.y, bw, 42, fill="#ffffff", stroke=LINE, line_width=0.6)
            c.text(x + 8, self.y + 5, s["role"], 7.8, color=GRAY)
            c.text(x + 8, self.y + 18, s.get("name") or "（待签）", 10.0,
                   color=INK if s.get("name") else NA)
            c.text(x + 8, self.y + 30, s.get("at") or "", 7.2, color=NA)
        self.y += 48

    def note(self, title, text):
        c = self.c
        self.ensure(46)
        c.text(M, self.y, title, 8.6, color=BRAND)
        self.y += 12
        self.y = c.paragraph(M, self.y, W - 2 * M, text, size=7.8, leading=11.4, gray=GRAY)
        self.y += 4

    def finish(self):
        self._footer()


def _bundle_items(bundle):
    plan = bundle["plan"]
    product = bundle["product"]
    return [
        ("产品名称", "%s %s" % (product.get("name", ""), product.get("model", ""))),
        ("产品工号", product.get("code", "")),
        ("客户", product.get("customer", "")),
        ("合同号", product.get("contract_no", "")),
        ("检验计划", plan.get("code", "")),
        ("标准依据", plan.get("std_ref", "")),
        ("报告编号", bundle.get("report_no", "")),
        ("生成时间", bundle.get("generated_at", "")),
    ]


def _compose(bundle, total_pages=None):
    plan = bundle["plan"]
    product = bundle["product"]
    nodes = bundle["nodes"]
    doc = ReportDoc(
        title="检验记录汇总表",
        footer_left="报告编号 %s · 计划 %s · 产品 %s · 记录快照 %s" % (
            bundle.get("report_no", ""), plan.get("code", ""), product.get("code", ""),
            bundle.get("snapshot", "")),
        total_pages=total_pages,
    )
    doc.meta_block(_bundle_items(bundle))

    counts = {"ok": 0, "over": 0, "pending": 0, "missing": 0, "na": 0}
    for idx, node in enumerate(nodes):
        rec = node.get("record") or {}
        judge = rec.get("judge") or {}
        rows = judge.get("rows") or []
        for r in rows:
            st = r.get("status", "na")
            counts[st] = counts.get(st, 0) + 1
        right = "判定 %s · %s · %s" % (
            VERDICT_TEXT.get(judge.get("overall"), "—"),
            rec.get("operator_name", "—"), (rec.get("created_at") or "")[:16])
        doc.section_bar("%s %s　模板 %s %s" % (
            CIRCLED[idx] if idx < len(CIRCLED) else str(idx + 1),
            node.get("name", ""), rec.get("template_code", ""), rec.get("template_revision", "")), right)

        table_rows = []
        for i, r in enumerate(rows, start=1):
            st = r.get("status", "na")
            table_rows.append([
                (str(i), GRAY, "center"),
                (r.get("label", ""), INK, "left"),
                (r.get("spec", ""), GRAY, "left"),
                (r.get("value_text", "") + ((" " + r["unit"]) if r.get("unit") else ""), INK, "right"),
                (STATUS_TEXT.get(st, "—"), STATUS_COLOR.get(st, INK), "center"),
            ])
        doc.table(["序号", "检验项目", "规格要求", "实测值", "判定"], table_rows,
                  weights=[26, 140, 150, 95, 56])

        if judge.get("counts", {}).get("over"):
            bad = [r for r in rows if r.get("status") == "over"]
            doc.callout("本表存在 %d 项超差" % len(bad),
                        "；".join("%s：%s（%s）" % (b["label"], b["value_text"], b["note"] or "超限")
                                  for b in bad[:3]), color=BAD, height=36)
        elif judge.get("counts", {}).get("pending"):
            doc.callout("本表有 %d 项待确认，未完成复核" % judge["counts"]["pending"], "", color=WARN, height=30)

    over_all, pend_all = counts["over"], counts["pending"] + counts["missing"]
    if over_all:
        verdict, color = "不合格（存在超差项）", BAD
    elif pend_all:
        verdict, color = "待完善（存在待确认 / 缺项）", WARN
    else:
        verdict, color = "合格", OK
    doc.callout("检验结论：%s" % verdict,
                "共检验 %d 项　合格 %d　超差 %d　待确认 %d　缺项 %d" % (
                    counts["ok"] + counts["over"] + counts["pending"] + counts["missing"],
                    counts["ok"], counts["over"], counts["pending"], counts["missing"]),
                color=color, height=42)

    signers = []
    first_rec = (nodes[0].get("record") or {}) if nodes else {}
    signers.append({"role": "质量工程师", "name": first_rec.get("operator_name", ""),
                    "at": (first_rec.get("created_at") or "")[:16]})
    sup = [s for s in bundle.get("signoffs", []) if s.get("role") == "supervisor"]
    signers.append({"role": "复核（室主任）",
                    "name": sup[0]["name"] if sup else "",
                    "at": (sup[0]["at"] or "")[:16] if sup else ""})
    signers.append({"role": "归档日期", "name": bundle.get("generated_at", "")[:10], "at": "质量管理部"})
    doc.sign_block(signers)

    doc.note("追溯说明",
             "本表数据来自现场采集终端：口报文本经结构化抽取后由质量工程师逐项确认，判定由服务端按模板规则执行；"
             "记录不可覆盖修改，复测以新版本追加；每张表单绑定模板版本、标准版本与记录快照，可按快照号复现当时判定。")
    doc.finish()
    return doc


def build_report(bundle, out_path):
    first = _compose(bundle, total_pages=None)
    pages = len(first.c.pages)
    final = _compose(bundle, total_pages=pages) if pages > 1 else first
    final.pdf.save(out_path)
    return out_path, pages


def preview_png(bundle, page_index=0, scale=1.8):
    doc = _compose(bundle, total_pages=None)
    return doc.pdf.render_png(page_index, scale=scale), len(doc.c.pages)
