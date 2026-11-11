"""Run management and employee browsers against a guarded disposable database."""
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
from wsgiref.simple_server import make_server, WSGIRequestHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["ASR_ENGINE"] = "stub"
from werkzeug.security import generate_password_hash
import app
import db
import seed
import management_ai


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def main():
    with tempfile.TemporaryDirectory(prefix="qc-management-ui-") as temporary:
        folder = Path(temporary)
        db.DATA_DIR, db.DB_PATH = temporary, str(folder / "management.db")
        db.AUDIO_DIR, db.REPORT_DIR = str(folder / "audio"), str(folder / "reports")
        app.GRAMMAR_DIR = str(folder / "grammar")
        db.init_db()
        password = secrets.token_urlsafe(20)
        profiles = [
            ("ADMIN", "管理验收员", "admin", "管理组"),
            ("CHIEF-A", "甲组主管", "supervisor", "甲班组"),
            ("WORKER-A", "甲组检验员", "inspector", "甲班组"),
            ("CHIEF-B", "乙组主管", "supervisor", "乙班组"),
            ("WORKER-B", "乙组检验员", "inspector", "乙班组"),
        ]
        ids = {}
        with closing(db.connect()) as connection:
            seed.seed_if_empty(connection)
            hashed = generate_password_hash(password, method="pbkdf2:sha256:1000")
            for account, name, role, department in profiles:
                uid = db.insert(connection, "users", {
                    "emp_no": account, "name": name, "role": role, "dept": department,
                })
                connection.execute(
                    "INSERT INTO user_credentials(user_id,password_hash,is_admin) VALUES(?,?,?)",
                    (uid, hashed, int(role == "admin")))
                ids[account] = uid
            connection.commit()

        def inspector(account):
            client = app.app.test_client()
            response = client.post("/api/login", json={"emp_no": account, "password": password})
            assert response.status_code == 200, response.get_json()
            return client

        def fill(data, document, failed=False):
            data.update(document_no=document, manufacturer="管理联调生产厂", grade="Q355B",
                        supply_quantity=20, inspected_quantity=5,
                        qualified_quantity=4 if failed else 5, rejected_quantity=1 if failed else 0,
                        inspection_mode="sampling", disposition="加倍抽检" if failed else "")
            data["values"] = {key: {"value": "合格", "source": "manual", "confirmed": True}
                              for key in ("cert_provided", "cert_performance", "identity_match",
                                          "cert_seal", "surface", "shape", "coil")}
            if failed:
                data["values"]["surface"]["value"] = "不合格"
            data["values"].update(
                thickness={"value": [5.64], "source": "manual", "confirmed": True},
                width={"value": [1811], "source": "manual", "confirmed": True})
            return data

        def new_record(client):
            standard = client.get("/api/steel/standards").get_json()["standards"][0]
            result = client.post("/api/steel/records", json={"standard_id": standard["id"]})
            assert result.status_code == 201, result.get_json()
            return result.get_json()["record"]

        def finalize(client, record, document, failed=False):
            response = client.post("/api/steel/records/%d/finalize" % record["id"],
                                   json={"version": record["version"],
                                         "data": fill(record["data"], document, failed)})
            assert response.status_code == 200, response.get_json()
            return response.get_json()

        worker_a, worker_b = inspector("WORKER-A"), inspector("WORKER-B")
        passed = finalize(worker_a, new_record(worker_a), "MGR-PASS-101")["record"]
        failed_response = finalize(worker_a, new_record(worker_a), "MGR-FAIL-202", True)
        failed = failed_response["record"]
        child = failed_response["followup"]
        finalize(worker_b, new_record(worker_b), "B-SECRET-303")
        followup = finalize(worker_a, child, "MGR-RETEST-202")["record"]
        draft = new_record(worker_a)
        nonce = secrets.token_hex(20)
        fixture = {"nonce": nonce, "passed": passed["id"], "failed": failed["id"],
                   "followup": followup["id"], "draft": draft["id"], "ids": ids, "ai_calls":0}
        def isolated_api(config, key, messages):
            # Only this disposable test process substitutes the paid provider.
            fixture['ai_calls'] += 1
            if config['model'] == 'simulate-error':
                raise management_ai.AIError('模拟 API 超时，请重试。')
            if '测试连接' in messages[-1]['content']:
                return '{"ok":true}'
            source = json.loads(messages[-1]['content'])
            references = [r['ref'] for r in source['records'][:1]]
            return json.dumps({'summary':'本期检验情况已汇总，请结合各班组待办核对。',
                'highlights':[{'text':'复检保留了原首检结果，可结合原单核查。','refs':references,'team_refs':[]}],
                'actions':[{'text':'请核对当前待办及对应处置记录。','refs':references,'team_refs':[]}],
                'limitations':'当前数据用于管理核查，不能推断异常原因。'},ensure_ascii=False)
        management_ai.transport = isolated_api
        app.app.add_url_rule("/_management_ui_fixture", view_func=lambda: fixture)
        with make_server("127.0.0.1", 0, app.app, handler_class=QuietHandler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                node = os.environ.get("QC_TEST_NODE", r"C:\Program Files\WindowsApps\OpenAI.CodexPrimaryRuntime.v26-1105-1297-0_26.1105.1297.0_x64__3k8sg7r9htsxt\dependencies\node\bin\node.exe")
                environment = dict(os.environ, QC_MANAGER_BASE="http://127.0.0.1:%d" % server.server_port,
                                   QC_MANAGER_NONCE=nonce, QC_MANAGER_PASSWORD=password,
                                   NO_PROXY="127.0.0.1,localhost", PYTHONIOENCODING="utf-8")
                result = subprocess.run([node, "tools/management_browser_check.cjs"], cwd=ROOT,
                                        env=environment, capture_output=True, text=True,
                                        encoding="utf-8", errors="replace", timeout=180)
                if result.returncode:
                    raise RuntimeError((result.stdout + result.stderr).strip())
                print(result.stdout.strip())
            finally:
                server.shutdown()
                thread.join(5)


if __name__ == "__main__":
    main()
