# -*- coding: utf-8 -*-
"""Fill the original XLSX package without round-tripping its native math drawing.

The spreadsheet library drops the template's Office Math width tolerance on
import/export. This narrow OOXML writer preserves native parts and cell styles.
No Excel/Node dependency is required on the deployed reporting server.
"""
import hashlib
import copy
import html
import io
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
import zipfile

import steel

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates/steel-incoming-reference.xlsx"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
      "xdr": "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing",
      "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "m": "http://schemas.openxmlformats.org/officeDocument/2006/math"}


def template_hash():
    return hashlib.sha256(TEMPLATE.read_bytes()).hexdigest()


def _write_cell(xml, address, value, rich=None):
    pattern = r'<c\b(?P<attrs>[^>]*\br="%s"[^>]*?)(?:/>|>.*?</c>)' % re.escape(address)
    def replacement(match):
        attrs = re.sub(r'\s+t="[^"]*"', "", match.group("attrs")).rstrip("/ ")
        if rich is not None:
            return '<c%s t="inlineStr"><is>%s</is></c>' % (attrs, rich)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not math.isfinite(value):
                raise ValueError("不能输出非有限数值")
            return '<c%s t="n"><v>%s</v></c>' % (attrs, format(value, ".15g"))
        return '<c%s t="inlineStr"><is><t xml:space="preserve">%s</t></is></c>' % (attrs, escape(str(value or "")))
    changed, count = re.subn(pattern, replacement, xml, count=1, flags=re.S)
    if count != 1:
        raise ValueError("模板缺少预期单元格 %s" % address)
    return changed


def _conclusion_rich(source, record, address):
    data = record["data"]
    # B13 is the short overall verdict. The original underlined quantity prose
    # (sampling or full inspection) occupies F13 in both modes.
    if record["status"] != "final" or address != "F13":
        return None
    strings = ET.fromstring(source.read("xl/sharedStrings.xml")).findall("s:si", NS)
    sampling = data["inspection_mode"] == "sampling"
    si = copy.deepcopy(strings[29 if sampling else 30])
    runs = si.findall("s:r", NS)
    if not sampling:
        numbers = iter([data["supply_quantity"], data["qualified_quantity"], data["rejected_quantity"]])
        for run in runs:
            text = run.find("s:t", NS)
            if run.find("s:rPr/s:u", NS) is not None and (text.text or "").strip().isdigit():
                text.text = steel.pretty(next(numbers))
    else:
        items = iter([steel.pretty(data["supply_quantity"]), data["sampling_basis"], steel.pretty(data["inspected_quantity"]),
                      steel.sampling_result(data),
                      data.get("disposition") or ("不合格" if data["rejected_quantity"] else "合格")])
        in_group = False
        for run in runs:
            underlined = run.find("s:rPr/s:u", NS) is not None
            if underlined:
                run.find("s:t", NS).text = " " if in_group else " " + next(items) + " "
                in_group = True
            else:
                in_group = False
    # Ordinary spaces introduced while filling the underlined blanks must be
    # significant, including runs containing only a single space. Native Excel
    # rejects the sampling inline rich string without this attribute, although
    # generic XML parsers can still read it. Keep the source fonts/underlines.
    for run in runs:
        run.find("s:t", NS).set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    ET.register_namespace("", NS["s"])
    return "".join(ET.tostring(run, encoding="unicode") for run in runs)


def _drawing(data, raw):
    for prefix, uri in list(NS.items()) + [("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
                                         ("a14", "http://schemas.microsoft.com/office/drawing/2010/main"),
                                         ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006")]:
        if prefix != "s":
            ET.register_namespace(prefix, uri)
    root = ET.fromstring(raw)
    for anchor in list(root):
        # The source signature belongs to a previous inspection, not this one.
        is_signature = any("电子签名" in node.get("descr", "") for node in anchor.iter())
        if is_signature or anchor.find("xdr:cxnSp", NS) is not None:
            root.remove(anchor)
    nominal = steel.pretty(data.get("width_nominal"))
    lower = steel.pretty(data.get("width_lower"))
    upper_number = data.get("width_upper")
    upper_sign = "-" if upper_number is not None and upper_number < 0 else "+"
    upper = steel.pretty(abs(upper_number)) if upper_number is not None else ""
    for formula in root.findall(".//m:sSubSup", NS):
        for branch, texts in (("e", [nominal]), ("sub", [lower]), ("sup", [upper_sign, upper])):
            nodes = formula.findall("m:%s/m:r/m:t" % branch, NS)
            for node, value in zip(nodes, texts):
                node.text = value
    for fallback in root.findall(".//{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"):
        nodes = fallback.findall(".//a:t", NS)
        if len(nodes) == 6:
            for node, value in zip(nodes, [nominal, "_", lower, "^", upper_sign, upper]):
                node.text = value
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_xlsx(record, template_bytes=None):
    template_bytes = TEMPLATE.read_bytes() if template_bytes is None else template_bytes
    if record.get("template_hash") and record["template_hash"] != hashlib.sha256(template_bytes).hexdigest():
        raise ValueError("本记录绑定的员工模板已变化，不能使用不同版式重新生成")
    cells = steel.cell_values(record)
    result = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(template_bytes)) as source, zipfile.ZipFile(result, "w", compression=zipfile.ZIP_DEFLATED) as target:
        sheet = source.read("xl/worksheets/sheet1.xml").decode("utf-8")
        for address, value in cells.items():
            sheet = _write_cell(sheet, address, value, _conclusion_rich(source, record, address))
        # Keep native sizes and original margins; explicitly fit this inspection
        # form to one A4 page instead of relying on the user's printer defaults.
        if "<pageSetUpPr" not in sheet:
            if re.search(r"<sheetPr\b[^>]*/>", sheet):
                sheet = re.sub(r"<sheetPr\b[^>]*/>", '<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>', sheet, count=1)
            elif "</sheetPr>" in sheet:
                sheet = sheet.replace("</sheetPr>", '<pageSetUpPr fitToPage="1"/></sheetPr>', 1)
            else:
                sheet = re.sub(r"(<worksheet\b[^>]*>)", r'\1<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>', sheet, count=1)
        page = '<pageSetup paperSize="9" orientation="portrait" fitToWidth="1" fitToHeight="1"/>'
        if re.search(r"<pageSetup\b", sheet):
            sheet = re.sub(r"<pageSetup\b[^>]*/>", page, sheet, count=1)
        else:
            sheet = re.sub(r"(<pageMargins\b[^>]*/>)", r"\1" + page, sheet, count=1)
        workbook = source.read("xl/workbook.xml").decode("utf-8")
        if "_xlnm.Print_Area" not in workbook:
            names = '<definedNames><definedName name="_xlnm.Print_Area" localSheetId="0">\'Sheet1\'!$A$1:$H$15</definedName></definedNames>'
            workbook = workbook.replace("<calcPr", names + "<calcPr", 1)
        for info in source.infolist():
            name = info.filename
            if name in ("xl/media/image1.jpeg", "xl/drawings/_rels/drawing1.xml.rels"):
                continue  # Do not retain the old signature even as an unused ZIP part.
            raw = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                raw = sheet.encode("utf-8")
            elif name == "xl/workbook.xml":
                raw = workbook.encode("utf-8")
            elif name == "xl/drawings/drawing1.xml":
                raw = _drawing(record["data"], raw)
            target.writestr(info, raw)
    return result.getvalue()


def template_cells(template_bytes=None):
    with zipfile.ZipFile(TEMPLATE if template_bytes is None else io.BytesIO(template_bytes)) as source:
        shared = ["".join(node.itertext()) for node in ET.fromstring(source.read("xl/sharedStrings.xml")).findall("s:si", NS)]
        root = ET.fromstring(source.read("xl/worksheets/sheet1.xml"))
        cells = {}
        for node in root.findall(".//s:sheetData/s:row/s:c", NS):
            value = node.find("s:v", NS)
            value = value.text if value is not None else ""
            cells[node.get("r")] = shared[int(value)] if node.get("t") == "s" and value else value
        return root, cells


def html_preview(record, template_bytes=None):
    root, cells = template_cells(template_bytes)
    cells.update(steel.cell_values(record))
    merges, covered = {}, set()
    def coord(address):
        match = re.fullmatch(r"([A-H])(\d+)", address)
        return ord(match[1]) - 65, int(match[2])
    for merge in root.findall("s:mergeCells/s:mergeCell", NS):
        a, b = map(coord, merge.get("ref").split(":"))
        merges[a] = (b[0] - a[0] + 1, b[1] - a[1] + 1)
        covered.update((c, r) for c in range(a[0], b[0] + 1) for r in range(a[1], b[1] + 1) if (c, r) != a)
    body = []
    data = record["data"]
    for row in root.findall("s:sheetData/s:row", NS):
        r = int(row.get("r"))
        tds = []
        for c in range(8):
            if (c, r) in covered:
                continue
            address = chr(65 + c) + str(r)
            value = cells.get(address, "")
            content = html.escape(str(value if value is not None else "")).replace("\n", "<br>")
            if address == "C10":
                content = '<span class="math">%s<span class="stack"><span>%s%s</span><span>%s</span></span></span>' % (
                    steel.pretty(data.get("width_nominal")), "+" if (data.get("width_upper") or 0) >= 0 else "",
                    steel.pretty(data.get("width_upper")), steel.pretty(data.get("width_lower")))
            w, h = merges.get((c, r), (1, 1))
            classes = []
            if address in ("B6", "B7", "B13", "F13", "B14"):
                classes.append("prose")
            if address == "A8":
                classes.append("vertical")
            if r <= 2:
                classes.append("top")
            tds.append('<td colspan="%d" rowspan="%d" class="%s">%s</td>' % (w, h, " ".join(classes), content))
        body.append('<tr style="height:%spt">%s</tr>' % (row.get("ht", "30"), "".join(tds)))
    if record["status"] == "final":
        state = "已确认记录"
    else:
        pending = set(data.get("voice_pending", [])) | steel.unconfirmed_asr_readings(data)
        pending.update(candidate["key"] for candidate in data.get("voice_candidates", []))
        state = "草稿预览 · 核对并生成后才能下载正式 Excel"
        if pending:
            state += " · %d 项语音待核对，候选值尚未写入表格" % len(pending)
    # Resolve from .../api/steel/records/<id>/preview so the link also works
    # when the application is mounted below a gateway prefix.
    return_route = "#/steel/" + str(int(record["id"])) if record.get("id") is not None else "#/steel"
    return '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>进货检验记录</title>
<style>body{margin:0;background:#e9edf1;color:#000}
.toolbar{position:sticky;top:0;z-index:1;background:white;border-bottom:1px solid #c9d4dd;padding:8px 14px;font:14px/1.4 sans-serif;color:#334858}
.toolbar nav{max-width:800px;margin:0 auto;display:flex;align-items:center;justify-content:space-between;gap:8px}
.toolbar a,.toolbar button{display:inline-flex;align-items:center;justify-content:center;box-sizing:border-box;min-height:44px;padding:8px 14px;border-radius:7px;font:inherit;text-decoration:none}
.toolbar a{background:#0e4265;color:white;font-weight:bold}.toolbar a[hidden]{display:none}.toolbar button{margin-left:auto;border:1px solid #b9c9d4;color:#123e5a;background:white}
.toolbar p{max-width:800px;margin:6px auto 0;overflow-wrap:anywhere}
.paper{background:white;margin:12px auto;padding:15px;max-width:800px}table{width:100%%;border-collapse:collapse;table-layout:fixed;font:12pt SimSun,"宋体",serif}td{border:1px solid #000;text-align:center;vertical-align:middle;padding:2px 5px;overflow-wrap:anywhere;box-sizing:border-box}.top{border:0}tr:first-child td{font-size:22pt;font-weight:bold}tr:nth-child(2) td{text-align:left;font-size:11pt}.prose{text-align:left;font-size:11pt;line-height:1.6}.vertical{writing-mode:vertical-rl;letter-spacing:4px}.math{display:inline-flex;align-items:center;font-family:"Cambria Math",serif}.stack{display:inline-flex;flex-direction:column;font-size:9pt;text-align:left;margin-left:3px;line-height:1.05}@media print{body{background:white}.toolbar{display:none}.paper{margin:0;padding:0;max-width:none}@page{size:A4 portrait;margin:12mm}table{font-size:10.5pt}.prose{font-size:10pt}tr{break-inside:avoid}}</style></head>
<body><header class="toolbar"><nav aria-label="表单预览操作"><a id="return-record" href="../../../../%s" target="_top">返回检验记录</a><button type="button" onclick="window.print()">打印预览</button></nav><p>%s · %s</p></header>
<main class="paper"><table><colgroup>%s</colgroup>%s</table></main>
<script>
const returnLink = document.getElementById('return-record');
function sameAppLocation(page) {
  return page.location.origin === returnLink.origin &&
    page.location.pathname.replace(/\\/$/, '') === returnLink.pathname.replace(/\\/$/, '');
}
if (window.parent !== window) {
  try {
    // The full-screen in-app preview already provides its own return action.
    returnLink.hidden = sameAppLocation(window.parent) &&
      window.parent.location.hash === returnLink.hash.replace('#/steel/', '#/steel-preview/');
  } catch (_) { /* Cross-origin embeds keep the ordinary link. */ }
}
returnLink.addEventListener('click', function (event) {
  if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
  const embedded = window.parent !== window;
  const editor = embedded ? window.parent : window.opener;
  try {
    if (!editor || editor.closed || !sameAppLocation(editor) || editor.location.hash !== returnLink.hash) return;
    if (embedded) {
      const field = editor.document.querySelector('.steel-workhead a');
      if (!field) return;
      field.focus();
      field.scrollIntoView({block: 'center'});
      event.preventDefault();
    } else {
      editor.focus();
      // Some mobile browsers retain the tab even after a user-initiated close.
      setTimeout(function () { if (!window.closed) location.replace(returnLink.href); }, 150);
      window.close();
      event.preventDefault();
    }
  } catch (_) { /* Unavailable or cross-origin opener: use the normal record link. */ }
});
</script></body></html>''' % (
        return_route, html.escape(state), html.escape(record.get("snapshot") or ""),
        "".join('<col style="width:%s%%">' % w for w in (12.45, 17.4, 12.63, 7.5, 6.83, 7.5, 15.54, 20.15)), "".join(body))
