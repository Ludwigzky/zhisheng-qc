"""Real password-login fixtures exclusively for disposable SQLite tests."""
import http.client
import json
import os
from pathlib import Path
import secrets
import tempfile
from urllib.parse import urlsplit
from werkzeug.security import generate_password_hash
import identity

INSPECTOR = "TEST-INSPECT"
SUPERVISOR = "TEST-SUPERVISE"
PROCESS = "TEST-PROCESS"
TEAM = "测试班组"
PASSWORD = secrets.token_urlsafe(28)
_hash = None


def install(c):
    global _hash
    data = Path(c.execute("PRAGMA database_list").fetchone()[2]).resolve(strict=True)
    if Path(tempfile.gettempdir()).resolve() not in data.parents or data.name == "demo.db":
        raise RuntimeError("测试账号仅能在临时目录的隔离测试库创建")
    if _hash is None:
        _hash = generate_password_hash(PASSWORD, method=identity.PASSWORD_METHOD)
    rows = [(INSPECTOR,"演示检验员","inspector"),(SUPERVISOR,"测试主管","supervisor"),(PROCESS,"测试工艺","process")]
    with c:
        for number, name, role in rows:
            cursor = c.execute("INSERT INTO users(emp_no,name,role,dept) VALUES(?,?,?,?)",(number,name,role,TEAM))
            c.execute("INSERT INTO user_credentials(user_id,password_hash) VALUES(?,?)",(cursor.lastrowid,_hash))
        c.execute("UPDATE plans SET workgroup=? WHERE workgroup=''",(TEAM,))


def login(client, number=INSPECTOR):
    response = client.post("/api/login", json={"emp_no":number,"password":PASSWORD})
    if response.status_code != 200:
        raise AssertionError(("测试账号登录失败",response.status_code,response.get_json()))
    return response


def isolated_origin():
    value = os.environ.get("QC_TEST_BASE","")
    url = urlsplit(value)
    nonce = os.environ.get("QC_TEST_NONCE","")
    if (not nonce or url.scheme!="http" or url.hostname!="127.0.0.1" or not url.port or
            url.username or url.password or url.path or url.query or url.fragment):
        raise SystemExit("请用 tools/asr_integration_check.py 启动隔离测试")
    connection = http.client.HTTPConnection(url.hostname,url.port,timeout=4)
    try:
        connection.request("GET","/_qc_http_fixture")
        response = connection.getresponse()
        if response.status != 200 or json.loads(response.read()) != {"nonce":nonce}:
            raise SystemExit("目标不是本轮隔离测试服务，已停止")
    finally:
        connection.close()
    return value
