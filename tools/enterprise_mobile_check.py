"""Run the employee enrollment and steel export UI flow on a fresh loopback service."""
import hashlib
import io
from contextlib import closing
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from wsgiref.simple_server import make_server, WSGIRequestHandler
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["ASR_ENGINE"] = "stub"
import app
import db
import seed


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="qc-enterprise-") as temp:
        folder = Path(temp)
        db.DATA_DIR, db.DB_PATH = temp, str(folder / "enterprise.db")
        db.AUDIO_DIR, db.REPORT_DIR = str(folder / "audio"), str(folder / "reports")
        app.GRAMMAR_DIR = str(folder / "grammar")
        db.init_db()
        with closing(db.connect()) as c:
            seed.seed_if_empty(c)
            assert c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        nonce = secrets.token_hex(16)
        app.app.add_url_rule("/_enterprise_ui_fixture", view_func=lambda: {"nonce":nonce})
        with make_server("127.0.0.1", 0, app.app, handler_class=QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                base = "http://127.0.0.1:%d" % server.server_port
                node = os.environ.get("QC_TEST_NODE", "node")
                env = dict(os.environ, QC_ENTERPRISE_BASE=base, QC_ENTERPRISE_NONCE=nonce,
                           QC_ENTERPRISE_DOWNLOAD=str(folder / "download.xlsx"), NO_PROXY="127.0.0.1,localhost")
                proc = subprocess.run([node, "tools/enterprise_mobile_check.cjs"], cwd=ROOT, env=env,
                                      capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
                if proc.returncode:
                    raise RuntimeError((proc.stdout + proc.stderr).strip())
                print(proc.stdout.strip())
            finally:
                server.shutdown()
                thread.join(5)
        payload = (folder / "download.xlsx").read_bytes()
        with closing(db.connect()) as c:
            archives=c.execute("SELECT xlsx_sha256,xlsx FROM steel_archives").fetchall()
            assert len(archives)==1 and archives[0]["xlsx"]==payload
            assert archives[0]["xlsx_sha256"] == hashlib.sha256(payload).hexdigest()
            assert c.execute("SELECT COUNT(*) FROM user_credentials").fetchone()[0] == 4
        with zipfile.ZipFile(io.BytesIO(payload)) as z:
            root=ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
            ns={"s":"http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
            def cell(address):
                node=root.find(".//s:c[@r='%s']"%address,ns)
                return "".join(entry.text or "" for entry in node.findall(".//s:t",ns)) or node.findtext("s:v",namespaces=ns)
            assert (cell("B3"),cell("B4"),cell("G9"),cell("G10"),cell("B15"))==(
                "E2E-117","测试生产厂","5.71","1806","真实检验员")
            conclusion = "".join(cell("F13").split())
            assert cell("B13") == "合格" and "抽检1件" in conclusion and "100%检验" not in conclusion
            assert "1～4件可抽检" in cell("B14") and "至少抽1件" in cell("B14")
        print("PASS: downloaded workbook matches the stored archive hash, document, measurements and signed employee.")


if __name__=="__main__":
    main()
