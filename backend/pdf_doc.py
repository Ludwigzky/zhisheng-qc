# -*- coding: utf-8 -*-
"""
极小 PDF 生成器（零第三方依赖，纯离线）。

特性
  * 内嵌系统 TrueType 中文字体（Identity-H / CIDFontType2），中文、数字、符号正常显示
  * 同一份绘制指令可同时导出 PDF 与 PNG 预览（PNG 用于自检和界面内嵌预览）
  * 支持横线、矩形、左/中/右对齐文本、自动换行、多页

坐标约定：内部使用 y 向下（左上角原点），写 PDF 时再翻转成 y 向上。
"""
import io
import os
import struct
import zlib

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\Deng.ttf",
    r"C:\Windows\Fonts\simkai.ttf",
    r"C:\Windows\Fonts\STSONG.TTF",
    r"C:\Windows\Fonts\simfang.ttf",
]


def pick_font(path=None):
    cands = ([path] if path else []) + FONT_CANDIDATES
    for p in cands:
        if p and os.path.exists(p):
            return p
    raise RuntimeError("未找到可用的中文字体（期望 C:\\Windows\\Fonts\\simhei.ttf）")


# --------------------------------------------------------------------------
# TrueType 解析（只取 PDF 需要的几项：cmap / hmtx / head / hhea / maxp）
# --------------------------------------------------------------------------
class TrueTypeFont(object):
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        tag = d[:4]
        if tag == b"ttcf":
            # 字体集合：取第一个字体的表目录
            num = struct.unpack(">I", d[8:12])[0]
            self.base = struct.unpack(">I", d[12:16])[0]
        else:
            self.base = 0
        self.tables = {}
        num_tables = struct.unpack(">H", d[self.base + 4:self.base + 6])[0]
        p = self.base + 12
        for _ in range(num_tables):
            t, checksum, off, length = struct.unpack(">4sIII", d[p:p + 16])
            self.tables[t] = (off, length)
            p += 16
        head = self.tables[b"head"][0]
        self.units_per_em = struct.unpack(">H", d[head + 18:head + 20])[0] or 1000
        xmin, ymin, xmax, ymax = struct.unpack(">hhhh", d[head + 36:head + 44])
        self.bbox = (xmin, ymin, xmax, ymax)
        self.index_to_loc_format = struct.unpack(">h", d[head + 50:head + 52])[0]
        hhea = self.tables[b"hhea"][0]
        self.ascent = struct.unpack(">h", d[hhea + 4:hhea + 6])[0]
        self.descent = struct.unpack(">h", d[hhea + 6:hhea + 8])[0]
        self.num_hmetrics = struct.unpack(">H", d[hhea + 34:hhea + 36])[0]
        maxp = self.tables[b"maxp"][0]
        self.num_glyphs = struct.unpack(">H", d[maxp + 4:maxp + 6])[0]
        self._advances = None
        self._cmap = None

    # ------------- 字形前进宽度 -------------
    @property
    def advances(self):
        if self._advances is None:
            off = self.tables[b"hmtx"][0]
            d = self.data
            adv = []
            last = 0
            for i in range(self.num_glyphs):
                if i < self.num_hmetrics:
                    last = struct.unpack(">H", d[off + 4 * i:off + 4 * i + 2])[0]
                adv.append(last)
            self._advances = adv
        return self._advances

    def width(self, gid):
        adv = self.advances
        if 0 <= gid < len(adv):
            return adv[gid]
        return 0

    # ------------- 字符 → 字形 -------------
    def _load_cmap(self):
        if self._cmap is not None:
            return
        d = self.data
        cmap_off = self.tables[b"cmap"][0]
        n = struct.unpack(">H", d[cmap_off + 2:cmap_off + 4])[0]
        best = None
        best_score = -1
        for i in range(n):
            pid, eid, off = struct.unpack(">HHI", d[cmap_off + 4 + 8 * i:cmap_off + 12 + 8 * i])
            sub = cmap_off + off
            fmt = struct.unpack(">H", d[sub:sub + 2])[0]
            score = 0
            if (pid, eid) in ((3, 10), (0, 4)):
                score = 12 if fmt == 12 else 10
            elif (pid, eid) in ((3, 1), (0, 3), (0, 1), (0, 0)):
                score = 8 if fmt == 4 else 6
            if score > best_score:
                best_score = score
                best = (fmt, sub)
        if best is None:
            raise RuntimeError("字体缺少可用的 cmap 子表")
        fmt, sub = best
        mapping = {}
        if fmt == 4:
            segx2 = struct.unpack(">H", d[sub + 6:sub + 8])[0]
            seg = segx2 // 2
            ends = struct.unpack(">%dH" % seg, d[sub + 14:sub + 14 + segx2])
            starts_off = sub + 16 + segx2
            starts = struct.unpack(">%dH" % seg, d[starts_off:starts_off + segx2])
            deltas_off = starts_off + segx2
            deltas = struct.unpack(">%dh" % seg, d[deltas_off:deltas_off + segx2])
            ranges_off = deltas_off + segx2
            ranges = struct.unpack(">%dH" % seg, d[ranges_off:ranges_off + segx2])
            for i in range(seg):
                start, end, delta, ro = starts[i], ends[i], deltas[i], ranges[i]
                if start == 0xFFFF:
                    continue
                for cp in range(start, end + 1):
                    if ro == 0:
                        gid = (cp + delta) & 0xFFFF
                    else:
                        idx = ranges_off + 2 * i + ro + 2 * (cp - start)
                        if idx + 2 > len(d):
                            continue
                        gid = struct.unpack(">H", d[idx:idx + 2])[0]
                        if gid:
                            gid = (gid + delta) & 0xFFFF
                    if gid:
                        mapping[cp] = gid
        elif fmt == 12:
            ngroups = struct.unpack(">I", d[sub + 12:sub + 16])[0]
            p = sub + 16
            for _ in range(ngroups):
                sc, ec, sg = struct.unpack(">III", d[p:p + 12])
                for cp in range(sc, min(ec, sc + 20000) + 1):
                    mapping[cp] = sg + (cp - sc)
                p += 12
        else:
            raise RuntimeError("不支持的 cmap 格式: %d" % fmt)
        self._cmap = mapping

    def gid(self, ch):
        self._load_cmap()
        return self._cmap.get(ord(ch), 0)

    def text_width(self, s, size):
        w = sum(self.width(self.gid(ch)) for ch in s)
        return w * size / float(self.units_per_em)


# --------------------------------------------------------------------------
# 画布：记录绘制指令，供 PDF / PNG 两种后端消费
# --------------------------------------------------------------------------
class Canvas(object):
    def __init__(self, width, height):
        self.w = width
        self.h = height
        self.pages = []          # [[op, ...], ...]
        self.ops = None
        self.font = None
        self.new_page()

    def new_page(self):
        self.ops = []
        self.pages.append(self.ops)
        return self

    # ---- 基础 ----
    def line(self, x1, y1, x2, y2, width=0.5, gray=0.0, color=None):
        self.ops.append(("line", x1, y1, x2, y2, width, gray if color is None else color))

    def rect(self, x, y, w, h, fill=None, stroke=None, line_width=0.5):
        self.ops.append(("rect", x, y, w, h, fill, stroke, line_width))

    def text(self, x, y, s, size=9, align="left", gray=0.0, bold=False, color=None):
        """y 为文本顶部，返回文本宽度。"""
        if s is None:
            s = ""
        s = str(s)
        tw = self.font.text_width(s, size) if self.font else len(s) * size * 0.5
        if align == "right":
            x = x - tw
        elif align == "center":
            x = x - tw / 2.0
        self.ops.append(("text", x, y, s, size, gray if color is None else color, bold))
        return tw

    def text_width(self, s, size=9):
        return self.font.text_width(str(s), size)

    # ---- 复合 ----
    def wrap(self, s, width, size):
        """按字符宽度折行（中文按字断行，西文尽量按空格断）。"""
        s = str(s or "")
        lines, cur = [], ""
        for ch in s:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if self.text_width(cur + ch, size) > width and cur:
                # 西文单词回退
                if ch.isalnum() and " " in cur:
                    head, _, tail = cur.rpartition(" ")
                    if head:
                        lines.append(head)
                        cur = tail + ch
                        continue
                lines.append(cur)
                cur = ch
            else:
                cur += ch
        if cur:
            lines.append(cur)
        return lines or [""]

    def paragraph(self, x, y, width, s, size=9, leading=None, gray=0.0):
        leading = leading or size * 1.55
        for ln in self.wrap(s, width, size):
            self.text(x, y, ln, size=size, gray=gray)
            y += leading
        return y


# --------------------------------------------------------------------------
# PDF 写出
# --------------------------------------------------------------------------
def _esc_name(s):
    return s.replace("\\", "").replace("/", "").replace(" ", "")


def _rgb(v):
    """颜色统一成 (r,g,b) 0~1：支持灰度浮点、'#0b3d66'、三元组。"""
    if isinstance(v, str):
        h = v.lstrip("#")
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
    if isinstance(v, (int, float)):
        f = float(v)
        return (f, f, f)
    r, g, b = v
    return (float(r), float(g), float(b))


def _fill(v):
    r, g, b = _rgb(v)
    return "%.3f %.3f %.3f rg" % (r, g, b)


def _stroke(v):
    r, g, b = _rgb(v)
    return "%.3f %.3f %.3f RG" % (r, g, b)


def _rgb255(v):
    r, g, b = _rgb(v)
    return (int(r * 255), int(g * 255), int(b * 255))


class PDF(object):
    def __init__(self, canvas, font_path=None, title="", subject=""):
        self.c = canvas
        self.font = TrueTypeFont(pick_font(font_path))
        canvas.font = self.font
        self.title = title
        self.subject = subject
        self._gids = set()
        self._used_cids = set()

    # ---- 文本编码 ----
    def _encode(self, s):
        out = []
        for ch in str(s):
            g = self.font.gid(ch)
            self._used_cids.add(g)
            out.append("%04X" % g)
        return "".join(out)

    def _em(self, v):
        """字体单位 → PDF 的千分之一 em（PDF 规范要求）。"""
        return int(round(v * 1000.0 / self.font.units_per_em))

    def _w_array(self):
        parts = []
        for gid in sorted(self._used_cids):
            parts.append("%d [%d]" % (gid, self._em(self.font.width(gid))))
        return "[" + " ".join(parts) + "]"

    def _to_unicode(self):
        lines = []
        cids = sorted(self._used_cids)
        for i in range(0, len(cids), 90):
            chunk = cids[i:i + 90]
            lines.append("%d beginbfchar" % len(chunk))
            for gid in chunk:
                ch = self._reverse.get(gid, 0xFFFD)
                lines.append("<%04X> <%04X>" % (gid, ord(ch)))
            lines.append("endbfchar")
        head = ("/CIDInit /ProcSet findresource begin\n12 dict begin\nbegincmap\n"
                "/CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def\n"
                "/CMapName /Adobe-Identity-UCS def\n/CMapType 2 def\n"
                "1 begincodespacerange\n<0000> <FFFF>\nendcodespacerange\n")
        tail = "\nendcmap\nCMapName currentdict /CMap defineresource pop\nend\nend\n"
        return head + "\n".join(lines) + tail

    def _content(self, page_ops):
        out = []
        for op in page_ops:
            kind = op[0]
            if kind == "line":
                _, x1, y1, x2, y2, lw, col = op
                out.append("q %s %.3f w %.2f %.2f m %.2f %.2f l S Q" %
                           (_stroke(col), lw, x1, self.c.h - y1, x2, self.c.h - y2))
            elif kind == "rect":
                _, x, y, w, h, fill, stroke, lw = op
                yy = self.c.h - y - h
                if fill is not None:
                    out.append("q %s %.2f %.2f %.2f %.2f re f Q" % (_fill(fill), x, yy, w, h))
                if stroke is not None:
                    out.append("q %s %.3f w %.2f %.2f %.2f %.2f re S Q" % (_stroke(stroke), lw, x, yy, w, h))
            elif kind == "text":
                _, x, y, s, size, col, bold = op
                if not s.strip():
                    continue
                baseline = y + self.font.ascent * size / float(self.font.units_per_em)
                out.append("BT /F1 %.2f Tf %s %.2f %.2f Td <%s> Tj ET" %
                           (size, _fill(col), x, self.c.h - baseline, self._encode(s)))
        return "\n".join(out)

    def save(self, path):
        # 先把所有页面的内容流生成出来：这一步会填充用到的字形集合，
        # 之后才能正确生成 /W 与 ToUnicode（顺序颠倒会导致映射缺失）。
        contents = []
        for page_ops in self.c.pages:
            contents.append(self._content(page_ops).encode("latin-1", "replace"))
        self._reverse = {}
        for page in self.c.pages:
            for op in page:
                if op[0] == "text":
                    for ch in op[3]:
                        g = self.font.gid(ch)
                        if g and g not in self._reverse:
                            self._reverse[g] = ch

        objs = []           # 1-based
        def add(body):
            objs.append(body)
            return len(objs)

        font_bytes = zlib.compress(self.font.data, 9)
        page_ids = []

        # 预留：1 Catalog, 2 Pages, 3 Type0, 4 CIDFont, 5 Descriptor, 6 FontFile, 7 ToUnicode
        obj_catalog = add(None)
        obj_pages = add(None)
        obj_type0 = add(None)
        obj_cid = add(None)
        obj_fd = add(None)
        obj_ff = add(b"<< /Length %d /Length1 %d /Filter /FlateDecode >>\nstream\n" %
                     (len(font_bytes), len(self.font.data)) + font_bytes + b"\nendstream")
        tu = self._to_unicode().encode("utf-8")
        obj_tu = add(b"<< /Length %d >>\nstream\n" % len(tu) + tu + b"\nendstream")

        for content in contents:
            comp = zlib.compress(content, 9)
            cid = add(b"<< /Length %d /Filter /FlateDecode >>\nstream\n" % len(comp) + comp + b"\nendstream")
            pid = add(("<< /Type /Page /Parent %d 0 R /MediaBox [0 0 %.2f %.2f] "
                       "/Resources << /Font << /F1 %d 0 R >> /ProcSet [/PDF /Text] >> "
                       "/Contents %d 0 R >>" %
                       (obj_pages, self.c.w, self.c.h, obj_type0, cid)).encode("latin-1"))
            page_ids.append(pid)

        objs[obj_catalog - 1] = ("<< /Type /Catalog /Pages %d 0 R >>" % obj_pages).encode("latin-1")
        kids = " ".join("%d 0 R" % p for p in page_ids)
        objs[obj_pages - 1] = ("<< /Type /Pages /Count %d /Kids [%s] >>" % (len(page_ids), kids)).encode("latin-1")
        base = _esc_name(os.path.splitext(os.path.basename(self.font.path))[0])
        objs[obj_type0 - 1] = ("<< /Type /Font /Subtype /Type0 /BaseFont /%s /Encoding /Identity-H "
                               "/DescendantFonts [%d 0 R] /ToUnicode %d 0 R >>" %
                               (base, obj_cid, obj_tu)).encode("latin-1")
        objs[obj_cid - 1] = ("<< /Type /Font /Subtype /CIDFontType2 /BaseFont /%s "
                             "/CIDSystemInfo << /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                             "/FontDescriptor %d 0 R /DW 1000 /W %s /CIDToGIDMap /Identity >>" %
                             (base, obj_fd, self._w_array())).encode("latin-1")
        bbox = " ".join(str(self._em(v)) for v in self.font.bbox)
        objs[obj_fd - 1] = ("<< /Type /FontDescriptor /FontName /%s /Flags 4 /FontBBox [%s] "
                            "/ItalicAngle 0 /Ascent %d /Descent %d /CapHeight %d /StemV 80 "
                            "/FontFile2 %d 0 R >>" %
                            (base, bbox, self._em(self.font.ascent), self._em(self.font.descent),
                             self._em(int(self.font.ascent * 0.7)), obj_ff)).encode("latin-1")

        # ---- 序列化 ----
        buf = io.BytesIO()
        buf.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = [0]
        for i, body in enumerate(objs, start=1):
            offsets.append(buf.tell())
            buf.write(b"%d 0 obj\n" % i)
            buf.write(body if body else b"null")
            buf.write(b"\nendobj\n")
        xref_pos = buf.tell()
        n = len(objs) + 1
        buf.write(b"xref\n0 %d\n" % n)
        buf.write(b"0000000000 65535 f \n")
        for off in offsets[1:]:
            buf.write(b"%010d 00000 n \n" % off)
        buf.write(b"trailer\n<< /Size %d /Root %d 0 R /Info << /Title <%s> /Producer (Weihua QC Demo) >> >>\n" %
                  (n, obj_catalog, self._utf16_hex(self.title)))
        buf.write(b"startxref\n%d\n%%%%EOF\n" % xref_pos)
        with open(path, "wb") as f:
            f.write(buf.getvalue())
        return path

    @staticmethod
    def _utf16_hex(s):
        return s.encode("utf-16-be").hex().upper().encode("latin-1")

    # ---- PNG 预览（自检 / 界面内嵌） ----
    def render_png(self, page_index=0, scale=2.0):
        from PIL import Image, ImageDraw, ImageFont
        c = self.c
        img = Image.new("RGB", (int(c.w * scale), int(c.h * scale)), "white")
        dr = ImageDraw.Draw(img)
        fpath = self.font.path
        cache = {}

        def font_of(size):
            key = round(size * scale, 2)
            if key not in cache:
                cache[key] = ImageFont.truetype(fpath, max(6, int(round(key))))
            return cache[key]

        for op in c.pages[page_index]:
            kind = op[0]
            if kind == "line":
                _, x1, y1, x2, y2, lw, col = op
                dr.line([x1 * scale, y1 * scale, x2 * scale, y2 * scale],
                        fill=_rgb255(col), width=max(1, int(round(lw * scale))))
            elif kind == "rect":
                _, x, y, w, h, fill, stroke, lw = op
                box = [x * scale, y * scale, (x + w) * scale, (y + h) * scale]
                fill_c = None if fill is None else _rgb255(fill)
                stroke_c = None if stroke is None else _rgb255(stroke)
                dr.rectangle(box, fill=fill_c, outline=stroke_c,
                             width=max(1, int(round(lw * scale))))
            elif kind == "text":
                _, x, y, s, size, col, bold = op
                f = font_of(size)
                dr.text((x * scale, y * scale), s, font=f, fill=_rgb255(col))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
