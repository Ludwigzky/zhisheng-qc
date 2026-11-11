# -*- coding: utf-8 -*-
"""Run HTTP/view regressions on a unique port and disposable database only."""
import json
import argparse
import os
import secrets
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import urllib.request
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import app
import db
import seed
import test_accounts


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-engine", action="store_true", help="Use the installed native Mandarin model")
    parser.add_argument("--mobile", action="store_true", help="Run isolated headless Android viewport checks")
    args = parser.parse_args()
    os.environ["ASR_ENGINE"] = "sherpa-onnx" if args.real_engine else "stub"
    with tempfile.TemporaryDirectory(prefix="qc-http-") as directory:
        # Port 0 lets the OS allocate an unused port; never reset an existing demo.
        httpd = make_server("127.0.0.1", 0, app.app, handler_class=QuietHandler)
        thread = None
        try:
            db.DATA_DIR = directory
            db.DB_PATH = str(Path(directory) / "test.db")
            db.AUDIO_DIR = str(Path(directory) / "audio")
            db.REPORT_DIR = str(Path(directory) / "reports")
            app.GRAMMAR_DIR = str(Path(directory) / "grammar")
            db.init_db()
            c = db.connect()
            seed.seed_if_empty(c)
            test_accounts.install(c)
            c.close()
            nonce = secrets.token_hex(24)
            app.app.add_url_rule("/_qc_http_fixture", view_func=lambda: {"nonce":nonce})
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            base = "http://127.0.0.1:%d" % httpd.server_port
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(base + "/api/me", timeout=10) as response:
                status = json.load(response)
            expected = (status["asr"].get("engine") == "sherpa-onnx" and status["asr"].get("available")) if args.real_engine else (
                status["asr"].get("engine") == "stub" and status["asr"].get("reason") == "手动指定 stub")
            if status.get("version") != app.APP_VERSION or not expected:
                raise RuntimeError("测试服务身份不符，拒绝继续")
            env = dict(os.environ, QC_TEST_BASE=base, QC_TEST_NONCE=nonce, QC_TEST_OUTPUT=directory,
                       QC_TEST_PASSWORD=test_accounts.PASSWORD, PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1,localhost")
            commands = [["node", "tools/mic_startup_check.cjs"],
                        ["node", "tools/steel_audio_check.cjs"],
                        ["node", "tools/url_prefix_check.cjs"],
                        [sys.executable, "tools/asr_http_check.py"],
                        [sys.executable, "tools/api_sweep.py"],
                        [sys.executable, "tools/asr_verdict_check.py"],
                        [sys.executable, "tools/steel_voice_check.py"],
                        [sys.executable, "tools/identity_check.py"],
                        [sys.executable, "tools/steel_standards_check.py"],
                        [sys.executable, "tools/workflow_check.py"],
                        [sys.executable, "tools/steel_archive_check.py"]]
            if args.mobile:
                bundled_node = Path(os.environ.get("QC_TEST_NODE", r"C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1105-1297-0_26.1105.1297.0_x64__3k8sg7r9htsxt\dependencies\node\bin\node.exe"))
                if not bundled_node.is_file():
                    raise RuntimeError("请设置 QC_TEST_NODE 为带 Playwright 运行时的 node.exe")
                env["QC_TEST_NODE"] = str(bundled_node)
                commands.append([sys.executable, "tools/enterprise_mobile_check.py"])
            for command in commands:
                completed = subprocess.run(command, cwd=str(ROOT), env=env, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                           errors="replace", timeout=180 if args.mobile else 100)
                if completed.returncode or "BAD " in completed.stdout:
                    print(completed.stdout)
                    raise RuntimeError("集成测试失败: %s" % " ".join(command))
                print("PASS: %s (%s, temporary database)" % (" ".join(command), base))
        finally:
            if thread is not None:
                httpd.shutdown()
                thread.join(timeout=10)
            httpd.server_close()


if __name__ == "__main__":
    main()
