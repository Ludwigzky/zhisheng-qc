# -*- coding: utf-8 -*-
"""Local accounts for the inspection application and invitation-only enrollment."""
import base64
import hashlib
import ipaddress
import re
import secrets
import time
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, request, session
from werkzeug.security import check_password_hash, generate_password_hash

import db


ROLES = ("inspector", "supervisor", "process")
INVITE_LIFETIME = 24 * 3600
SESSION_LIFETIME = 12 * 3600
PASSWORD_METHOD = "pbkdf2:sha256:600000"
DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(24), method=PASSWORD_METHOD)
USER_COLUMNS = "u.id,u.emp_no,u.name,u.role,u.dept,u.note,c.is_admin"


def active_user(conn, uid):
    return db.row(conn, "SELECT " + USER_COLUMNS + """ FROM users u JOIN user_credentials c
        ON c.user_id=u.id WHERE u.id=? AND c.enabled=1""", (uid,))


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def current_user(conn):
    token = session.get("auth_token")
    if not isinstance(token, str) or len(token) != 43:
        return None
    return db.row(conn, "SELECT " + USER_COLUMNS + """ FROM auth_sessions s
        JOIN users u ON u.id=s.user_id JOIN user_credentials c ON c.user_id=u.id
        WHERE s.token_hash=? AND s.expires_at>? AND c.enabled=1""",
                  (_digest(token), int(time.time())))


def _event(conn, actor, action, target):
    # Share the transaction with the account operation.
    db.insert(conn, "audit", {"at": db.now(), "actor_id": actor["id"], "actor_name": actor["name"],
                             "action": action, "target": target, "detail": "{}"})


def _new_session(conn, uid):
    token = secrets.token_urlsafe(32)
    now = int(time.time())
    old = session.get("auth_token")
    if isinstance(old, str):
        conn.execute("DELETE FROM auth_sessions WHERE token_hash=?", (_digest(old),))
    conn.execute("DELETE FROM auth_sessions WHERE expires_at<=?", (now,))
    conn.execute("INSERT INTO auth_sessions(token_hash,user_id,expires_at) VALUES(?,?,?)",
                 (_digest(token), uid, now + SESSION_LIFETIME))
    session.clear()
    session["auth_token"] = token
    return active_user(conn, uid)


def _is_local_setup_request():
    try:
        peer = ipaddress.ip_address(request.remote_addr or "")
        host = (urlsplit(request.host_url).hostname or "").lower()
        local_host = host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False
    forwarded = any(h.lower().startswith("x-forwarded-") or h.lower() in
                    ("forwarded", "via", "x-real-ip") for h in request.headers.keys())
    return peer.is_loopback and local_host and not forwarded


def _input():
    value = request.get_json(silent=True)
    if not isinstance(value, dict):
        raise ValueError("请提交有效的账号信息")
    return value


def _text(body, key, label, limit):
    value = body.get(key)
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= limit or not value.strip().isprintable():
        raise ValueError("请输入有效的%s（最多%d个字符）" % (label, limit))
    return value.strip()


def _employee(body):
    value = _text(body, "emp_no", "账号", 32).upper()
    if not re.fullmatch(r"[A-Z0-9][A-Z0-9_.-]{1,31}", value):
        raise ValueError("账号为2至32位字母、数字、点、横线或下划线")
    return value


def _password(body):
    password = body.get("password")
    if not isinstance(password, str) or not 8 <= len(password) <= 128 or not password.strip():
        raise ValueError("密码需为8至128个字符")
    return password


def _new_user(conn, emp, name, dept, role, hashed, admin=False):
    uid = db.insert(conn, "users", {"emp_no": emp, "name": name, "dept": dept, "role": role, "note": ""})
    db.insert(conn, "user_credentials", {"user_id": uid, "password_hash": hashed, "is_admin": int(admin), "enabled": 1})
    return uid


def _new_invite(conn, actor, emp, name, dept, role, reset_uid=None):
    now = int(time.time())
    code = base64.b32encode(secrets.token_bytes(10)).decode("ascii")
    conn.execute("UPDATE auth_invites SET used_at=? WHERE emp_no=? AND used_at IS NULL", (now, emp))
    iid = db.insert(conn, "auth_invites", {"code_hash": _digest(code), "emp_no": emp, "name": name,
                    "dept": dept, "role": role, "issued_by": actor["id"], "expires_at": now + INVITE_LIFETIME,
                    "reset_user_id": reset_uid})
    _event(conn, actor, "account_reset_invite" if reset_uid else "account_invite", "invite:%d" % iid)
    return {"id": iid, "code": code, "emp_no": emp, "expires_at": now + INVITE_LIFETIME}


def _too_many(conn, buckets):
    now = int(time.time())
    for key, limit in buckets:
        row = db.row(conn, "SELECT failures FROM auth_limits WHERE bucket=? AND expires_at>?", (key, now))
        if row and row["failures"] >= limit:
            return True
    return False


def _failed(conn, buckets):
    now = int(time.time())
    with conn:
        conn.execute("DELETE FROM auth_limits WHERE expires_at<=?", (now,))
        for key, _ in buckets:
            conn.execute("""INSERT INTO auth_limits(bucket,failures,expires_at) VALUES(?,1,?)
                          ON CONFLICT(bucket) DO UPDATE SET failures=failures+1""", (key, now + 600))


def register(app, conn):
    bp = Blueprint("identity", __name__)

    @bp.before_request
    def require_json_origin():
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if ((origin and origin != request.host_url.rstrip("/")) or
                    request.headers.get("Sec-Fetch-Site") == "cross-site"):
                return jsonify(error="请从本应用页面操作"), 403
            if request.content_length and request.content_length > 8192:
                return jsonify(error="账号请求过大"), 413
            if not request.is_json:
                return jsonify(error="请提交 JSON 数据"), 415

    @bp.after_request
    def prevent_cache(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.errorhandler(ValueError)
    def invalid(exc):
        return jsonify(error=str(exc)), 400

    def admin_or_error():
        actor = current_user(conn())
        if actor is None:
            return None, (jsonify(error="请先登录", code="NO_AUTH"), 401)
        if not actor["is_admin"]:
            return None, (jsonify(error="仅部长可以管理账号"), 403)
        return actor, None

    def setup_required(c):
        return not db.row(c, "SELECT 1 AS present FROM user_credentials WHERE is_admin=1 LIMIT 1")

    @bp.route("/api/auth/setup-state")
    def setup_state():
        return jsonify(setup_required=setup_required(conn()), local_setup_allowed=_is_local_setup_request())

    @bp.route("/api/auth/setup", methods=["POST"])
    def setup():
        if not _is_local_setup_request():
            return jsonify(error="请在运行服务的电脑上用 localhost 或 127.0.0.1 创建首位部长"), 403
        c = conn()
        if not setup_required(c):
            return jsonify(error="部长已创建，请登录"), 409
        body = _input()
        emp, name, dept = _employee(body), _text(body, "name", "姓名", 48), _text(body, "dept", "班组", 48)
        hashed = generate_password_hash(_password(body), method=PASSWORD_METHOD)
        with c:
            c.execute("BEGIN IMMEDIATE")
            if not setup_required(c):
                return jsonify(error="部长已创建，请登录"), 409
            if db.row(c, "SELECT id FROM users WHERE upper(emp_no)=?", (emp,)):
                return jsonify(error="账号已占用，请换一个账号"), 409
            uid = _new_user(c, emp, name, dept, "admin", hashed, admin=True)
            user = _new_session(c, uid)
            _event(c, user, "account_setup", "user:%d" % uid)
        return jsonify(user=user), 201

    @bp.route("/api/login", methods=["POST"])
    def login():
        c = conn()
        body = _input()
        emp = _employee(body)
        password = body.get("password")
        buckets = [("login-ip:" + _digest(request.remote_addr or ""), 80),
                   ("login-user:" + _digest((request.remote_addr or "") + ":" + emp), 8)]
        if _too_many(c, buckets):
            return jsonify(error="登录尝试过多，请10分钟后再试"), 429
        account = db.row(c, """SELECT u.id,c.password_hash FROM users u JOIN user_credentials c
                  ON c.user_id=u.id WHERE u.emp_no=? AND c.enabled=1""", (emp,))
        valid = isinstance(password, str) and 1 <= len(password) <= 128 and check_password_hash(
            account["password_hash"] if account else DUMMY_HASH, password)
        if not valid or not account:
            _failed(c, buckets)
            return jsonify(error="账号或密码不正确，或账号已停用"), 401
        with c:
            c.execute("BEGIN IMMEDIATE")
            # A reset or disable during the password check must win.
            if not db.row(c, "SELECT 1 FROM user_credentials WHERE user_id=? AND enabled=1 AND password_hash=?",
                          (account["id"], account["password_hash"])):
                return jsonify(error="账号状态已变更，请重新登录"), 401
            c.execute("DELETE FROM auth_limits WHERE bucket=?", (buckets[-1][0],))
            user = _new_session(c, account["id"])
            _event(c, user, "login", "user:%d" % account["id"])
        return jsonify(user=user)

    @bp.route("/api/logout", methods=["POST"])
    def logout():
        token = session.get("auth_token")
        if isinstance(token, str):
            with conn():
                conn().execute("DELETE FROM auth_sessions WHERE token_hash=?", (_digest(token),))
        session.clear()
        return jsonify(ok=True)

    @bp.route("/api/admin/accounts")
    def accounts():
        actor, err = admin_or_error()
        if err:
            return err
        c = conn()
        users = db.rows(c, "SELECT " + USER_COLUMNS + """,c.enabled FROM users u
                    JOIN user_credentials c ON c.user_id=u.id ORDER BY u.id""")
        invites = db.rows(c, """SELECT id,emp_no,name,dept,role,expires_at,reset_user_id FROM auth_invites
                    WHERE used_at IS NULL AND expires_at>? ORDER BY id DESC""", (int(time.time()),))
        return jsonify(users=users, invites=invites)

    @bp.route("/api/admin/invites", methods=["POST"])
    def invite():
        actor, err = admin_or_error()
        if err:
            return err
        body = _input()
        emp, name, dept = _employee(body), _text(body, "name", "姓名", 48), _text(body, "dept", "班组", 48)
        role = body.get("role")
        if role not in ROLES:
            return jsonify(error="请选择质量工程师、室主任或工艺人员"), 400
        c = conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            if db.row(c, "SELECT id FROM users WHERE upper(emp_no)=?", (emp,)):
                return jsonify(error="该账号已占用，请换一个账号或选择重设密码"), 409
            result = _new_invite(c, actor, emp, name, dept, role)
        return jsonify(result), 201

    @bp.route("/api/admin/invites/<int:iid>/revoke", methods=["POST"])
    def revoke(iid):
        actor, err = admin_or_error()
        if err:
            return err
        c = conn()
        with c:
            if not c.execute("UPDATE auth_invites SET used_at=? WHERE id=? AND used_at IS NULL",
                             (int(time.time()), iid)).rowcount:
                return jsonify(error="邀请码已使用或不存在"), 409
            _event(c, actor, "account_invite_revoke", "invite:%d" % iid)
        return jsonify(ok=True)

    @bp.route("/api/auth/register", methods=["POST"])
    def enroll():
        c = conn()
        body = _input()
        code = _text(body, "code", "邀请码", 64).replace("-", "").replace(" ", "").upper()
        password = _password(body)
        buckets = [("invite-ip:" + _digest(request.remote_addr or ""), 20)]
        if _too_many(c, buckets):
            return jsonify(error="邀请码尝试过多，请10分钟后重试"), 429
        token_hash = _digest(code)
        if not db.row(c, "SELECT id FROM auth_invites WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
                      (token_hash, int(time.time()))):
            _failed(c, buckets)
            return jsonify(error="邀请码无效、已使用或已过期"), 400
        hashed = generate_password_hash(password, method=PASSWORD_METHOD)
        with c:
            c.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            item = db.row(c, "SELECT * FROM auth_invites WHERE code_hash=? AND used_at IS NULL AND expires_at>?",
                          (token_hash, now))
            if not item:
                return jsonify(error="邀请码无效、已使用或已过期"), 400
            uid = item["reset_user_id"]
            if uid:
                if not c.execute("UPDATE user_credentials SET password_hash=?,enabled=1 WHERE user_id=? AND is_admin=0",
                                 (hashed, uid)).rowcount:
                    return jsonify(error="无法重设该账号"), 409
                c.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
            else:
                if db.row(c, "SELECT id FROM users WHERE upper(emp_no)=?", (item["emp_no"],)):
                    return jsonify(error="该账号已占用，请联系部长"), 409
                uid = _new_user(c, item["emp_no"], item["name"], item["dept"], item["role"], hashed)
            c.execute("UPDATE auth_invites SET used_at=? WHERE id=?", (now, item["id"]))
            user = _new_session(c, uid)
            _event(c, user, "account_reset" if item["reset_user_id"] else "account_register", "user:%d" % uid)
        return jsonify(user=user), 201

    @bp.route("/api/admin/accounts/<int:uid>/<action>", methods=["POST"])
    def manage_account(uid, action):
        actor, err = admin_or_error()
        if err:
            return err
        if action not in ("disable", "enable", "reset"):
            return jsonify(error="未知操作"), 404
        c = conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            target = db.row(c, """SELECT u.*,c.is_admin FROM users u JOIN user_credentials c
                                  ON c.user_id=u.id WHERE u.id=?""", (uid,))
            if not target:
                return jsonify(error="账号不存在"), 404
            if target["is_admin"]:
                return jsonify(error="不能在此处停用或重设首位部长"), 403
            if action == "reset":
                result = _new_invite(c, actor, target["emp_no"], target["name"], target["dept"], target["role"], uid)
                # Old sessions and the previous password cease to work immediately.
                c.execute("UPDATE user_credentials SET enabled=0,password_hash=? WHERE user_id=?", (DUMMY_HASH, uid))
            else:
                c.execute("UPDATE user_credentials SET enabled=? WHERE user_id=?", (int(action == "enable"), uid))
                c.execute("UPDATE auth_invites SET used_at=? WHERE reset_user_id=? AND used_at IS NULL",
                          (int(time.time()), uid))
                result = {"ok": True}
                _event(c, actor, "account_" + action, "user:%d" % uid)
            c.execute("DELETE FROM auth_sessions WHERE user_id=?", (uid,))
        return jsonify(result)

    app.register_blueprint(bp)
