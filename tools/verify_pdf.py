# -*- coding: utf-8 -*-
"""
PDF 结构自检：不依赖任何第三方 PDF 库，直接核对 PDF 规范里最容易出错的三处
  1. xref 偏移是否都指向对应 obj（错一个，任何阅读器都打不开）
  2. 文本宽度：/W 数组换算成 1/1000 em 后，与画布期望宽度是否一致（错则字挤/字散）
  3. 字体度量与字形覆盖：Ascent/Descent/BBox 是否已换算到 1/1000 em，字符是否都有字形
"""
import re
import struct
import zlib


def _parse_objects(data):
    m = re.search(rb"startxref\s+(\d+)", data)
    if not m:
        raise AssertionError("找不到 startxref")
    xref_off = int(m.group(1))
    seg = data[xref_off:data.find(b"trailer", xref_off)]
    head = re.search(rb"xref\s+(\d+)\s+(\d+)", seg)
    if not head:
        raise AssertionError("找不到 xref 表头")
    count = int(head.group(2))
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf])", seg)
    if len(entries) < count:
        raise AssertionError("xref 条目数不足: %d < %d" % (len(entries), count))
    objs = {}
    for i in range(1, count):
        off = int(entries[i][0])
        assert entries[i][2] == b"n", "对象 %d 未标记为在用" % i
        assert data[off:off + 20].startswith(b"%d 0 obj" % i), "xref 偏移错误: 对象 %d" % i
        end = data.find(b"endobj", off)
        objs[i] = data[off:end]
    return objs


def _stream(obj_bytes):
    i = obj_bytes.find(b"stream")
    j = obj_bytes.find(b"endstream")
    raw = obj_bytes[i + 6:j]
    raw = raw.lstrip(b"\r\n")
    try:
        return zlib.decompress(raw)
    except Exception:
        return raw


def _dict_after(obj_bytes, key):
    m = re.search(rb"/" + key + rb"\s+(\d+)\s+0\s+R", obj_bytes)
    return int(m.group(1)) if m else None


def verify(pdf_path, canvas, pdf):
    problems = []
    with open(pdf_path, "rb") as f:
        data = f.read()

    objs = _parse_objects(data)

    # ---- 找到页面与字体资源 ----
    pages = [i for i, o in objs.items() if b"/Type /Page " in o or b"/Type /Page/" in o]
    if not pages:
        problems.append("没有 Page 对象")

    font_id = None
    for i in pages:
        m = re.search(rb"/Font\s*<<\s*/F1\s+(\d+)\s+0\s+R", objs[i])
        if m:
            font_id = int(m.group(1))
            break
    if font_id is None:
        problems.append("页面资源里找不到 /F1")
        return problems, {}

    type0 = objs[font_id]
    cid_id = int(re.search(rb"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R", type0).group(1))
    tu_id = int(re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", type0).group(1))
    fd_id = int(re.search(rb"/FontDescriptor\s+(\d+)\s+0\s+R", objs[cid_id]).group(1))

    # ---- 1. CIDFont 宽度表 ----
    wm = re.search(rb"/W\s*\[(.*?)\]\s*/CIDToGIDMap", objs[cid_id], re.S)
    if not wm:
        problems.append("CIDFont 缺少 /W 或 /CIDToGIDMap")
        return problems, {}
    widths = {}
    for gid, w in re.findall(rb"(\d+)\s*\[\s*(\d+)\s*\]", wm.group(1)):
        widths[int(gid)] = int(w)

    dw_m = re.search(rb"/DW\s+(\d+)", objs[cid_id])
    dw = int(dw_m.group(1)) if dw_m else 1000

    # ---- 2. ToUnicode ----
    tu = _stream(objs[tu_id]).decode("latin-1")
    g2u = {}
    for gid, uni in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4})>", tu):
        g2u[int(gid, 16)] = chr(int(uni, 16))

    # ---- 3. 字体度量是否换算到 1000 em ----
    fd = objs[fd_id]
    asc = int(re.search(rb"/Ascent\s+(-?\d+)", fd).group(1))
    desc = int(re.search(rb"/Descent\s+(-?\d+)", fd).group(1))
    bbox = [int(x) for x in re.search(rb"/FontBBox\s*\[([^\]]*)\]", fd).group(1).split()]
    if not (300 <= asc <= 1200):
        problems.append("Ascent 未按 1000em 换算: %d" % asc)
    if not (30 <= abs(desc) <= 600):
        problems.append("Descent 未按 1000em 换算: %d" % desc)
    if max(abs(v) for v in bbox) < 300:
        problems.append("FontBBox 未按 1000em 换算: %s" % bbox)

    # ---- 4. 内嵌字体完整性 ----
    ff_id = _dict_after(fd, b"FontFile2")
    if ff_id is None:
        problems.append("FontDescriptor 缺少 FontFile2")
    else:
        ff = _stream(objs[ff_id])
        if ff.lstrip()[:4] != pdf.font.data[:4] or len(ff) != len(pdf.font.data):
            problems.append("内嵌字体字节与原字体不一致")
        if zlib.decompress(objs[ff_id].split(b"stream")[1].strip(b"\r\n").split(b"endstream")[0]) != pdf.font.data:
            problems.append("FontFile2 解压结果异常")

    # ---- 5. 逐个文本指令核对宽度与字形 ----
    checked = 0
    for page_ops in canvas.pages:
        for op in page_ops:
            if op[0] != "text" or not op[3].strip():
                continue
            s, size = op[3], op[4]
            total = 0
            for ch in s:
                gid = pdf.font.gid(ch)
                if gid == 0:
                    problems.append("字符无字形: %r" % ch)
                total += widths.get(gid, dw)
            got = total * size / 1000.0
            exp = pdf.font.text_width(s, size)
            if abs(got - exp) > 0.6:
                problems.append("宽度不一致 %r: pdf=%.2f expect=%.2f" % (s[:14], got, exp))
            checked += 1

    # ---- 6. 内容流里的字形都能反查到 unicode ----
    for i in pages:
        cid = _dict_after(objs[i], b"Contents")
        if cid is None:
            problems.append("页面缺少 Contents")
            continue
        content = _stream(objs[cid]).decode("latin-1")
        for hx in re.findall(r"<([0-9A-Fa-f]+)>\s*Tj", content):
            for k in range(0, len(hx), 4):
                gid = int(hx[k:k + 4], 16)
                if gid and gid not in g2u:
                    problems.append("ToUnicode 缺少 gid %d" % gid)

    stats = {"pages": len(pages), "glyphs": len(widths), "text_ops": checked,
             "ascent": asc, "descent": desc, "bbox": bbox, "dw": dw}
    return problems, stats


def check_glyph_coverage(chars):
    """返回字体里没有字形的字符列表。"""
    from pdf_doc import TrueTypeFont, pick_font
    f = TrueTypeFont(pick_font())
    return sorted({c for c in chars if c.strip() and f.gid(c) == 0})


# --------------------------------------------------------------------------
# 独立渲染：只读 PDF 字节（内容流 + /W + ToUnicode），不借助生成端的内存对象。
# 若渲染结果与版式预期一致，说明 PDF 在合规阅读器里的呈现也应当一致。
# --------------------------------------------------------------------------
TOKEN = re.compile(r"<[0-9A-Fa-f]+>|[-+]?\d*\.?\d+|[A-Za-z*'\"]+")


def render_from_pdf(pdf_path, font_path, page_no=1, scale=2.0):
    from PIL import Image, ImageDraw, ImageFont

    with open(pdf_path, "rb") as f:
        data = f.read()
    objs = _parse_objects(data)
    pages = [i for i, o in objs.items() if b"/Type /Page " in o]
    page = objs[pages[page_no - 1]]
    mb = [float(x) for x in re.search(rb"/MediaBox\s*\[([^\]]*)\]", page).group(1).split()]
    pw, ph = mb[2], mb[3]
    font_id = int(re.search(rb"/Font\s*<<\s*/F1\s+(\d+)\s+0\s+R", page).group(1))
    cid_id = int(re.search(rb"/DescendantFonts\s*\[\s*(\d+)\s+0\s+R", objs[font_id]).group(1))
    tu_id = int(re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", objs[font_id]).group(1))
    widths = {int(g): int(w) for g, w in
              re.findall(rb"(\d+)\s*\[\s*(\d+)\s*\]", re.search(rb"/W\s*\[(.*?)\]", objs[cid_id], re.S).group(1))}
    dw = 1000
    g2u = {}
    for gid, uni in re.findall(r"<([0-9A-Fa-f]{4})>\s*<([0-9A-Fa-f]{4})>",
                               _stream(objs[tu_id]).decode("latin-1")):
        g2u[int(gid, 16)] = chr(int(uni, 16))
    content = _stream(objs[_dict_after(page, b"Contents")]).decode("latin-1")

    img = Image.new("RGB", (int(pw * scale), int(ph * scale)), "white")
    dr = ImageDraw.Draw(img)
    cache = {}

    def font_of(pt):
        k = max(6, int(round(pt * scale)))
        if k not in cache:
            cache[k] = ImageFont.truetype(font_path, k)
        return cache[k]

    stack = []
    fill_c, stroke_c = (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    lw = 0.5
    tx = ty = 0.0
    size = 10.0
    path = []
    pending_rect = None
    for tok in TOKEN.findall(content):
        if tok[0] == "<":
            stack.append(("hex", tok[1:-1]))
            continue
        try:
            stack.append(float(tok))
            continue
        except ValueError:
            pass
        op = tok
        if op == "g":
            v = stack.pop()
            fill_c = (v, v, v)
        elif op == "G":
            v = stack.pop()
            stroke_c = (v, v, v)
        elif op == "rg":
            b = stack.pop(); g_ = stack.pop(); r = stack.pop()
            fill_c = (r, g_, b)
        elif op == "RG":
            b = stack.pop(); g_ = stack.pop(); r = stack.pop()
            stroke_c = (r, g_, b)
        elif op == "w":
            lw = stack.pop()
        elif op == "re":
            h, w, y, x = stack.pop(), stack.pop(), stack.pop(), stack.pop()
            pending_rect = (x, y, w, h)
        elif op in ("f", "B"):
            if pending_rect:
                x, y, w, h = pending_rect
                x0, y0 = x * scale, (ph - y - h) * scale
                dr.rectangle([x0, y0, x0 + w * scale, y0 + h * scale],
                             fill=tuple(int(v * 255) for v in fill_c),
                             outline=tuple(int(v * 255) for v in stroke_c),
                             width=max(1, int(round(lw * scale))))
                pending_rect = None
        elif op == "m":
            y, x = stack.pop(), stack.pop()
            path = [(x * scale, (ph - y) * scale)]
        elif op == "l":
            y, x = stack.pop(), stack.pop()
            path.append((x * scale, (ph - y) * scale))
        elif op == "S":
            if len(path) >= 2:
                dr.line(path, fill=tuple(int(v * 255) for v in stroke_c),
                        width=max(1, int(round(lw * scale))))
            path = []
        elif op == "Tf":
            size = stack.pop()
            stack.pop()
        elif op == "Td":
            ty_ = stack.pop()
            tx_ = stack.pop()
            tx, ty = tx_, ty_
        elif op == "Tj":
            item = stack.pop()
            hx = item[1] if isinstance(item, tuple) else ""
            s = "".join(g2u.get(int(hx[k:k + 4], 16), "?") for k in range(0, len(hx), 4))
            dr.text((tx * scale, (ph - ty) * scale), s, font=font_of(size),
                    fill=tuple(int(v * 255) for v in fill_c), anchor="ls")
            adv = sum(widths.get(int(hx[k:k + 4], 16), dw) for k in range(0, len(hx), 4))
            tx += adv * size / 1000.0
        elif op in ("BT", "ET"):
            stack = []
        elif op in ("q", "Q", "f"):
            pass

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
