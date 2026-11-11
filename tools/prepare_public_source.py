"""Export source for publication without modifying a running installation's data/template.

The reporting module's narrow OOXML writer is reused so native Office Math is
preserved. A template library round trip would discard the width tolerance.
The destination must be empty or contain only .git from an empty-repository clone.
"""
import argparse
import io
from pathlib import Path
import re
import shutil
import sys
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import steel
import steel_report


def public_template():
    data = steel.blank_data()
    data.update(document_no="", manufacturer="", grade="", inspection_date="")
    record = {"data": data, "status": "draft", "inspector_name": "", "template_hash": steel_report.template_hash()}
    built = steel_report.build_xlsx(record)
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(built)) as source, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            name = info.filename
            if name.startswith("xl/media/") or name == "docProps/custom.xml":
                continue
            raw = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                xml = raw.decode("utf-8")
                for address in ("B3", "D3", "H3", "B4", "D4", "H4", "G9", "G10", "B13", "F13", "B15", "G15"):
                    xml = steel_report._write_cell(xml, address, "")
                raw = xml.encode("utf-8")
            elif name == "xl/sharedStrings.xml":
                # Keep indices 29/30: the report filler uses their rich-text runs.
                root = ET.fromstring(raw)
                ns = steel_report.NS
                for index in (8, 35):
                    for node in root[index].findall(".//s:t", ns):
                        node.text = ""
                for index in (17, 20):
                    for node in root[index].findall(".//s:t", ns):
                        node.text = (node.text or "").replace("☑", "□")
                for run in root[30].findall("s:r", ns):
                    text = run.find("s:t", ns)
                    if text is not None and run.find("s:rPr/s:u", ns) is not None and (text.text or "").strip().isdigit():
                        text.text = "0"
                ET.register_namespace("", ns["s"])
                raw = ET.tostring(root, encoding="utf-8", xml_declaration=True)
            elif name == "docProps/core.xml":
                # Preserve QName prefixes in xsi:type values, which ET does not rewrite.
                raw = re.sub(rb"(<dc:creator>).*?(</dc:creator>)", rb"\1zhisheng-qc\2", raw, flags=re.S)
                raw = re.sub(rb"(<cp:lastModifiedBy>).*?(</cp:lastModifiedBy>)", rb"\1zhisheng-qc\2", raw, flags=re.S)
            elif name == "[Content_Types].xml":
                raw = re.sub(rb'<Override\b[^>]*PartName="/docProps/custom\.xml"[^>]*/>', b"", raw)
            elif name == "_rels/.rels":
                raw = re.sub(rb'<Relationship\b[^>]*Target="docProps/custom\.xml"[^>]*/>', b"", raw)
            target.writestr(info, raw)
    return out.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    destination = args.out.resolve()
    if destination == ROOT or ROOT in destination.parents:
        raise SystemExit("Destination must be outside the running project.")
    destination.mkdir(parents=True, exist_ok=True)
    if any(path.name != ".git" for path in destination.iterdir()):
        raise SystemExit("Destination must be empty or contain only .git.")
    for name in ("backend", "frontend", "tools", "docs", "models"):
        def ignore(folder, entries):
            return [entry for entry in entries if entry in ("__pycache__", "qc-demo-ca.cer", "sample_report.pdf", "sample_report.png")
                    or entry.endswith((".pyc", ".pyo", ".onnx", ".part", ".log"))]
        shutil.copytree(ROOT / name, destination / name, ignore=ignore)
    for source in ROOT.iterdir():
        if source.is_file() and (source.suffix in (".bat", ".ps1", ".json", ".md", ".txt") or source.name in (".gitignore", ".gitattributes")):
            shutil.copy2(source, destination / source.name)
    template = destination / "templates" / steel_report.TEMPLATE.name
    template.parent.mkdir()
    template.write_bytes(public_template())
    print("Public source exported:", destination)


if __name__ == "__main__":
    main()
