# -*- coding: utf-8 -*-
"""
质检现场采集 Demo · 后端 API（Flask + SQLite，本地运行，AI解读可选外部API）

业务链路：
  登录（本地账号）→ 选产品/检验计划（ITP）→ 领任务（按工序顺序）
  → 口报/键盘录入（真实中文数字与单位归一 + 槽位抽取）
  → 提交后服务端按模板规则判定（版本快照）
  → 不合格可复测（追加记录，不改历史）
  → 三张表单齐套 + 室主任签字 → 生成按规格排版的 PDF 检验记录
"""
import io
import json
import os
import re
import secrets
import math
import sys
import traceback
import uuid
from datetime import datetime

from flask import Flask, abort, g, jsonify, make_response, request, send_file, send_from_directory, session
from werkzeug.exceptions import HTTPException

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)

import asr_engine  # noqa: E402
import asr_grammar  # noqa: E402
import audio  # noqa: E402
import cnnum  # noqa: E402
import db  # noqa: E402
import access
import identity
import workflow
import management
import management_cases
import management_ai
import judge as judge_mod  # noqa: E402
import lab  # noqa: E402
import report as report_mod  # noqa: E402
import seed as seed_mod  # noqa: E402
import steel_routes  # noqa: E402
import steel_recovery  # noqa: E402
import microphone_help  # noqa: E402

FRONTEND = os.path.join(ROOT, "frontend")
APP_VERSION = "0.21.0-ai"

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("QC_SECRET") or secrets.token_bytes(32)
app.config["JSON_AS_ASCII"] = False
app.config["MAX_CONTENT_LENGTH"] = 24 * 1024 * 1024
app.config["SESSION_COOKIE_NAME"] = "weihua_qc_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# --------------------------------------------------------------------------
# 基础设施
# --------------------------------------------------------------------------
def conn():
    if "conn" not in g:
        g.conn = db.connect()
    return g.conn


@app.teardown_appcontext
def _close(exc):
    c = g.pop("conn", None)
    if c is not None:
        c.close()


def me():
    return identity.current_user(conn())


@app.before_request
def protect_work_objects():
    endpoint = request.endpoint or ""
    args = request.view_args or {}
    if request.method not in ("GET","HEAD","OPTIONS"):
        origin = request.headers.get("Origin")
        if origin and origin.rstrip("/") != request.host_url.rstrip("/"):
            abort(403, "请求必须来自当前应用")
        if request.headers.get("Sec-Fetch-Site")=="cross-site":
            abort(403, "请在本应用内操作")
    pid = None
    if endpoint in {"api_plan","api_sign","api_submit","work.configure"}:
        pid = args["pid"]
    elif endpoint in {"api_task","work.claim","work.handoff","api_record","api_task_grammar","api_sync_template","api_parse","api_audio","api_asr_mode"}:
        tid = args.get("tid")
        if tid is None:
            payload = request.form if endpoint=="api_audio" else request.get_json(silent=True)
            tid = payload.get("task_id") if hasattr(payload,"get") else None
        if tid is None: return
        if not str(tid).isdigit(): abort(400,"任务编号无效")
        row = db.row(conn(),"SELECT plan_id,assignee_id,status FROM tasks WHERE id=?",(tid,))
        if not row: abort(404)
        pid = row["plan_id"]
        if endpoint in ("api_parse","api_audio") and me() and (me()["role"]!="inspector" or row["assignee_id"]!=me()["id"] or row["status"]=="pass"):
            abort(403,"仅任务领取人可录入")
    elif endpoint in {"api_report_pdf","api_report_preview"}:
        row = db.row(conn(),"SELECT plan_id FROM reports WHERE id=?",(args["rid"],))
        if not row: abort(404)
        pid = row["plan_id"]
    if pid is not None and me() and not access.can_plan(conn(),me(),pid):
        abort(403,"不在本账号的检验计划范围内")


def need_login(fn):
    def wrapper(*a, **kw):
        u = me()
        if not u:
            return jsonify({"error": "未登录", "code": "NO_AUTH"}), 401
        return fn(*a, **kw)
    wrapper.__name__ = fn.__name__
    return wrapper


def need_role(*roles):
    def deco(fn):
        def wrapper(*a, **kw):
            u = me()
            if not u:
                return jsonify({"error": "未登录", "code": "NO_AUTH"}), 401
            if roles and u["role"] not in roles:
                return jsonify({"error": "当前角色无权限：%s" % u["role"], "code": "NO_PERM"}), 403
            return fn(*a, **kw)
        wrapper.__name__ = fn.__name__
        return wrapper
    return deco


@app.errorhandler(Exception)
def _err(exc):
    if isinstance(exc, tuple):
        return exc
    code = getattr(exc, "code", 500)
    if isinstance(exc, HTTPException):
        return jsonify({"error": exc.description}), code
    traceback.print_exc()
    return jsonify({"error": str(exc), "code": "SERVER_ERROR"}), 500


# --------------------------------------------------------------------------
# 数据装配
# --------------------------------------------------------------------------
def tpl_of(tid):
    r = db.row(conn(), "SELECT * FROM form_templates WHERE id=?", (tid,))
    if not r:
        return None
    r["schema"] = json.loads(r["schema_json"])
    return r


def records_of(task_id):
    rs = db.rows(conn(), "SELECT * FROM records WHERE task_id=? ORDER BY round", (task_id,))
    out = []
    for r in rs:
        r = dict(r)
        r["values"] = json.loads(r["values_json"] or "{}")
        r["judge"] = json.loads(r["judge_json"] or "{}")
        op = db.row(conn(), "SELECT name FROM users WHERE id=?", (r["operator_id"],))
        r["operator_name"] = op["name"] if op else ""
        out.append(r)
    return out


def task_view(task):
    tpl = tpl_of(task["template_id"])
    recs = records_of(task["id"])
    latest = recs[-1] if recs else None
    assignee = db.row(conn(), "SELECT name FROM users WHERE id=?", (task["assignee_id"],)) \
        if task["assignee_id"] else None
    return {
        "id": task["id"],
        "plan_id": task["plan_id"],
        "seq": task["seq"],
        "status": task["status"],
        "template": {"id": tpl["id"], "code": tpl["code"], "revision": tpl["revision"],
                     "title": tpl["title"], "std_version": tpl["std_version"],
                     "spec_ref": tpl["spec_ref"], "schema": tpl["schema"]},
        "assignee": assignee["name"] if assignee else None,
        "assignee_id": task["assignee_id"], "version": task["version"],
        "predecessors": workflow.predecessors(task), "blocked_by": workflow.check_prerequisites(conn(),task),
        "claimed_at": task["claimed_at"],
        "done_at": task["done_at"],
        "round": latest["round"] if latest else 0,
        "latest": latest,
        "history": [{"id": r["id"], "round": r["round"], "overall": r["overall"],
                     "created_at": r["created_at"], "snapshot": r["snapshot"],
                     "template_revision": r["template_revision"],
                     "counts": (r["judge"] or {}).get("counts", {})} for r in recs],
    }


def plan_view(plan_id, with_records=True):
    p = db.row(conn(), "SELECT * FROM plans WHERE id=?", (plan_id,))
    if not p:
        return None
    prod = db.row(conn(), "SELECT * FROM products WHERE id=?", (p["product_id"],))
    nodes = db.rows(conn(), "SELECT * FROM plan_nodes WHERE plan_id=? ORDER BY seq", (plan_id,))
    out_nodes = []
    for n in nodes:
        t = db.row(conn(), "SELECT * FROM tasks WHERE node_id=?", (n["id"],))
        tv = task_view(t)
        out_nodes.append({
            "node_id": n["id"], "seq": n["seq"], "name": n["name"], "short_name": n["short_name"],
            "task_id": t["id"], "task_status": t["status"], "template_code": tv["template"]["code"],
            "predecessors": tv["predecessors"], "blocked_by": tv["blocked_by"], "assignee":tv["assignee"],
            "template_revision": tv["template"]["revision"],
            "round": tv["round"],
            "overall": (tv["latest"] or {}).get("overall"),
            "record": tv["latest"] if with_records else None,
            "operator": tv["latest"]["operator_name"] if tv["latest"] else None,
        })
    passed = sum(1 for n in out_nodes if n["task_status"] == "pass")
    signs = db.rows(conn(), "SELECT * FROM signoffs WHERE plan_id=? ORDER BY id", (plan_id,))
    rep = db.row(conn(), "SELECT * FROM reports WHERE plan_id=? ORDER BY id DESC", (plan_id,))
    return {
        "plan": p, "product": prod, "nodes": out_nodes,
        "progress": {"passed": passed, "total": len(out_nodes)},
        "signoffs": signs, "report": rep,
        "can_sign": passed == len(out_nodes) and not any(s["role"] == "supervisor" for s in signs),
        "can_submit": passed == len(out_nodes) and any(s["role"] == "supervisor" for s in signs),
    }


def report_bundle(plan_id):
    pv = plan_view(plan_id)
    nodes = []
    for n in pv["nodes"]:
        rec = n.get("record") or {}
        nodes.append({
            "seq": n["seq"], "name": n["name"],
            "record": {
                "judge": rec.get("judge"),
                "template_code": n["template_code"],
                "template_revision": rec.get("template_revision"),
                "operator_name": rec.get("operator_name"),
                "created_at": rec.get("created_at"),
                "snapshot": rec.get("snapshot"),
            } if rec else None,
        })
    snap = "-".join((n["record"] or {}).get("snapshot", "")[:6] for n in nodes if n["record"])
    return {
        "plan": pv["plan"], "product": pv["product"], "nodes": nodes,
        "signoffs": pv["signoffs"],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "snapshot": snap,
    }


# --------------------------------------------------------------------------
# 静态页
# --------------------------------------------------------------------------
@app.route("/")
def index():
    response = make_response(send_from_directory(FRONTEND, "index.html"))
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/<path:name>")
def static_files(name):
    full = os.path.join(FRONTEND, name)
    if os.path.isfile(full):
        resp = make_response(send_from_directory(FRONTEND, name))
        resp.headers["Cache-Control"] = "no-store"
        return resp
    return jsonify({"error": "not found"}), 404


# --------------------------------------------------------------------------
# 登录
# --------------------------------------------------------------------------
@app.route("/api/me")
def api_me():
    u = me()
    return jsonify({"user": u, "asr": asr_engine.status(),
                    "version": APP_VERSION, "capabilities": {"management": True, "management_api": 1}})


@app.route("/api/bootstrap")
@need_login
def api_bootstrap():
    c = conn()
    users = db.rows(c, """SELECT u.id,u.name,u.role FROM users u JOIN user_credentials a ON a.user_id=u.id
                          WHERE a.enabled=1 AND u.dept=? AND u.role='inspector' ORDER BY u.name""",(me()["dept"],))
    prods = db.rows(c, "SELECT * FROM products ORDER BY id")
    plans = []
    for p in prods:
        pl = db.row(c, "SELECT * FROM plans WHERE product_id=? ORDER BY id", (p["id"],))
        if not pl or not access.can_plan(c,me(),pl["id"]):
            continue
        steps = db.rows(c, """SELECT t.status,n.short_name AS name FROM tasks t JOIN plan_nodes n ON n.id=t.node_id
                              WHERE t.plan_id=? ORDER BY t.seq""", (pl["id"],))
        done = sum(s["status"]=="pass" for s in steps)
        plans.append({"plan_id": pl["id"], "code": pl["code"], "product": p,
                      "passed": done, "total": len(steps), "steps":steps, "workgroup":pl["workgroup"], "status": pl["status"]})
    notes = db.rows(c, "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC LIMIT 12",
                    (me()["id"],))
    audits = access.audit_rows(c,me(),limit=10)
    return jsonify({"user": me(), "users": users, "plans": plans,
                    "notifications": notes, "audits": audits})


# --------------------------------------------------------------------------
# 任务
# --------------------------------------------------------------------------
@app.route("/api/tasks/<int:tid>")
@need_login
def api_task(tid):
    t = db.row(conn(), "SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    plan = plan_view(t["plan_id"], with_records=False)
    return jsonify({"task": task_view(t), "plan": plan})


@app.route("/api/tasks/<int:tid>/records", methods=["POST"])
@need_role("inspector")
def api_record(tid):
    u = me()
    body = request.get_json(force=True, silent=True) or {}
    if not isinstance(body, dict): abort(400,"请提交本轮检测值")
    values = body.get("values") or {}
    confirmed = body.get("confirmed") or []
    transcript = body.get("transcript") or ""
    audio_id = body.get("audio_id")
    c = conn()
    t = workflow.begin_write(c,tid,body)
    if t["assignee_id"]!=u["id"]: abort(403,"请先领取任务，只能提交本人的任务")
    if audio_id and not db.row(c,"SELECT 1 FROM audio_assets WHERE name=? AND owner_id=? AND task_id=?",(audio_id,u["id"],tid)):
        abort(400,"录音不属于本项任务")
    tpl = tpl_of(t["template_id"])
    if not isinstance(values,dict) or not isinstance(confirmed,list): abort(400,"检测数据格式无效")
    fields = {f["key"]:f for f in judge_mod.flatten_fields(tpl["schema"])}
    for key,meta in values.items():
        if key not in fields or not isinstance(meta,dict): abort(400,"存在未知或无效检测字段")
        if fields[key].get("type") in ("enum","text") or meta.get("value") in (None,""): continue
        try:
            if isinstance(meta["value"],bool) or not math.isfinite(float(meta["value"])):
                raise ValueError()
        except (KeyError,TypeError,ValueError,OverflowError):
            abort(400,fields[key].get("label",key)+"需要有效的有限数值")
    result = judge_mod.judge(tpl["schema"], values, confirmed)

    prev = db.rows(c, "SELECT * FROM records WHERE task_id=? ORDER BY round", (tid,))
    rnd = (prev[-1]["round"] + 1) if prev else 1
    rid = db.insert(c, "records", {
        "task_id": tid, "round": rnd, "operator_id": u["id"], "created_at": db.now(),
        "transcript": transcript, "engine": body.get("engine") or "manual",
        "audio_id": audio_id, "template_id": tpl["id"], "template_revision": tpl["revision"],
        "std_version": tpl["std_version"],
        "values_json": json.dumps(values, ensure_ascii=False),
        "judge_json": json.dumps(result, ensure_ascii=False),
        "overall": result["overall"], "snapshot": result["snapshot"],
    })
    status = {"pass": "pass", "fail": "fail", "pending": "pending"}[result["overall"]]
    c.execute("UPDATE tasks SET status=?, latest_record_id=?, assignee_id=?, done_at=?,version=version+1 WHERE id=?",
              (status, rid, u["id"], db.now() if status == "pass" else None, tid))
    c.commit()

    if result["overall"] == "fail":
        bad = [r for r in result["rows"] if r["status"] == "over"]
        db.notify(c, u["id"], "over",
                  "第 %d 道工序判定超差" % t["seq"],
                  "；".join("%s %s（%s）" % (b["label"], b["value_text"], b["note"]) for b in bad[:3]))
        for sup in db.rows(c, """SELECT u.id FROM users u JOIN user_credentials a ON a.user_id=u.id
                                  WHERE u.role='supervisor' AND u.dept=? AND a.enabled=1""",(u["dept"],)):
            db.notify(c, sup["id"], "over", "超差预警：工序 %d" % t["seq"],
                      "质量工程师 %s 提交的记录存在超差项，需跟进复测" % u["name"])
    db.audit(c, u, "submit_record", "task:%d" % tid,
             {"round": rnd, "overall": result["overall"], "snapshot": result["snapshot"]})

    rec = db.row(c, "SELECT * FROM records WHERE id=?", (rid,))
    rec["values"] = json.loads(rec["values_json"])
    rec["judge"] = json.loads(rec["judge_json"])
    rec["operator_name"] = u["name"]
    return jsonify({
        "record": rec,
        "task": task_view(db.row(c, "SELECT * FROM tasks WHERE id=?", (tid,))),
        "plan": plan_view(t["plan_id"], with_records=False),
        "message": judge_mod.summary_line(result),
    })


@app.route("/api/asr/parse", methods=["POST"])
@need_login
def api_parse():
    body = request.get_json(force=True, silent=True) or {}
    tid = body.get("task_id")
    text = body.get("text") or ""
    current = body.get("current_key")
    t = db.row(conn(), "SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    tpl = tpl_of(t["template_id"])
    fields = judge_mod.flatten_fields(tpl["schema"])
    confidence = body.get("asr_confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
            if not 0 <= confidence <= 1:
                raise ValueError()
        except (TypeError, ValueError):
            return jsonify({"error": "asr_confidence 必须在 0 到 1 之间"}), 400
    parsed = cnnum.parse_utterance(text, fields, current, asr_confidence=confidence)
    parsed["engine"] = asr_engine.engine_name()
    return jsonify(parsed)


@app.route("/api/asr/status")
@need_login
def api_asr_status():
    return jsonify(asr_engine.status())


# --------------------------------------------------------------------------
# 语法约束识别：把表单编成 grxml，让引擎只在「这张表的标签+数字+枚举」里搜
# --------------------------------------------------------------------------
GRAMMAR_DIR = os.path.join(db.DATA_DIR, "grammar")
_GRAMMAR_STATE = {"path": None, "mode": None, "error": None}


def ensure_grammar(schema, current_key=None):
    with asr_engine.SESSION_LOCK:
        return _ensure_grammar(schema, current_key)


def _ensure_grammar(schema, current_key=None):
    """确保 worker 加载了与当前模板匹配的语法；同版本直接命中缓存。"""
    if not schema:
        return {"ok": False, "error": "没有模板"}
    try:
        path, items = asr_grammar.write_grammar(schema, GRAMMAR_DIR, current_key=current_key)
    except Exception as exc:
        return {"ok": False, "error": "生成语法失败: %s" % exc}
    engine = asr_engine.get_engine()
    if (engine.available and engine.mode == "grammar"
            and getattr(engine, "grammar", None) == os.path.abspath(path)):
        return {"ok": True, "path": path, "items": items, "cached": True, "mode": "grammar"}
    res = asr_engine.load_grammar(path)
    _GRAMMAR_STATE["path"] = path
    _GRAMMAR_STATE["mode"] = res.get("mode")
    _GRAMMAR_STATE["error"] = res.get("error")
    return {"ok": bool(res.get("ok")), "path": path, "items": items,
            "error": res.get("error"), "mode": res.get("mode"), "cached": False}


@app.route("/api/tasks/<int:tid>/grammar")
@need_login
def api_task_grammar(tid):
    t = db.row(conn(), "SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    tpl = tpl_of(t["template_id"])
    res = ensure_grammar(tpl["schema"])
    res["template"] = {"code": tpl["code"], "revision": tpl["revision"]}
    db.audit(conn(), me(), "asr_grammar", "task:%d" % tid,
             {"ok": res.get("ok"), "items": res.get("items"), "error": (res.get("error") or "")[:120]})
    return jsonify(res)


@app.route("/api/asr/mode", methods=["POST"])
@need_login
def api_asr_mode():
    body = request.get_json(force=True, silent=True) or {}
    mode = (body.get("mode") or "").strip()
    tid = body.get("task_id")
    schema = None
    if tid:
        t = db.row(conn(), "SELECT * FROM tasks WHERE id=?", (tid,))
        if t:
            schema = tpl_of(t["template_id"])["schema"]
    if mode == "dictation":
        res = asr_engine.set_dictation()
        _GRAMMAR_STATE["mode"] = res.get("mode")
        return jsonify(dict(res, mode="dictation"))
    if mode == "grammar":
        if schema is None:
            schema = seed_mod.TPL_IPQC
        return jsonify(ensure_grammar(schema))
    return jsonify({"error": "未知模式，只能是 grammar 或 dictation"}), 400


@app.route("/api/asr/probe", methods=["POST"])
@need_login
def api_asr_probe():
    """重新检测识别引擎（清缓存重试），页面上“重新检测”按钮用。"""
    info = asr_engine.probe(reset=True)
    db.audit(conn(), me(), "asr_probe", "engine:%s" % info.get("engine"),
             {"available": info.get("available"), "reason": (info.get("reason") or "")[:120]})
    return jsonify(info)


# --------------------------------------------------------------------------
# 语音：上传 → 质量分析 →（可选）降噪 → 识别
# --------------------------------------------------------------------------
def _save_upload(f):
    db.ensure_dirs()
    try:
        raw = f.read()
    finally:
        f.close()
    try:
        with audio.wave.open(io.BytesIO(raw),"rb") as w:
            frames, rate, channels, width = w.getnframes(), w.getframerate(), w.getnchannels(), w.getsampwidth()
            if not (1 <= channels <= 8 and width in (1,2,4) and 4000 <= rate <= 192000 and frames>0):
                raise ValueError("录音参数无效")
            if frames > rate * 30:
                raise ValueError("录音超过 30 秒，请分段录入")
            if len(w.readframes(frames)) != frames * channels * width:
                raise ValueError("录音文件不完整")
    except (audio.wave.Error,EOFError):
        raise ValueError("需要可解码的 PCM WAV 录音")
    ext = ".wav"
    aid = uuid.uuid4().hex[:12]
    path = os.path.join(db.AUDIO_DIR, aid + ext)
    with open(path, "wb") as out:
        out.write(raw)
    return aid + ext, path, True, len(raw)


def _prep_16k(path, aid):
    sr, x = audio.read_wav(path)
    if sr != audio.TARGET_SR:
        sr, x = audio.to_mono16k(sr, x)
    limit = audio.TARGET_SR * 30
    if len(x) > limit:
        raise ValueError("录音超过 30 秒，请一项一句分段录音（不会截断后继续填表）")
    p16 = os.path.join(db.AUDIO_DIR, aid.replace(".wav", "") + "_16k.wav")
    audio.write_wav(p16, audio.TARGET_SR, x)
    return p16, x


def _process_audio(want_denoise, schema=None):
    # 引擎常驻且语法是全局状态：配置、原声和降噪两次识别必须属于同一请求。
    with asr_engine.SESSION_LOCK:
        try:
            result = _process_audio_locked(want_denoise, schema)
        except ValueError as exc:
            return {"error": str(exc)}
        if result.get("audio_id") and not result.get("error") and me():
            c = conn()
            with c:
                db.insert(c,"audio_assets",dict(name=result["audio_id"],owner_id=me()["id"],
                      steel_id=request.form.get("record_id") if request.endpoint=="steel.audio" else None,
                      task_id=request.form.get("task_id") if request.endpoint=="api_audio" else None))
        return result


def _branch_values(result, schema, current_key):
    fields = judge_mod.flatten_fields(schema or {})
    parsed = cnnum.parse_utterance(result.get("text") or "", fields, current_key)
    return {a["key"]: a["value"] for a in parsed["assignments"] if not a.get("derived")}


def _process_audio_locked(want_denoise, schema=None):
    """公共处理链：留档 → 指标 →（语法约束）→ 降噪分支 → 识别 → 择优。"""
    f = request.files.get("audio")
    if not f:
        return {"error": "没有音频"}
    mode = request.form.get("mode") or "system"
    asr_mode = request.form.get("asr_mode") or "grammar"
    current_key = request.form.get("current_key") or None
    if asr_mode not in ("grammar", "dictation"):
        return {"error": "未知识别模式"}
    editable = [f for f in judge_mod.flatten_fields(schema or {}) if not f.get("derived")]
    if current_key and current_key not in {f["key"] for f in editable}:
        return {"error": "当前字段不在表单中"}
    # Names, dates and identifiers require open dictation even with SAPI:
    # the restricted measurement grammar deliberately omits text fields.
    if any(f["key"] == current_key and f.get("type") == "text" for f in editable):
        asr_mode = "dictation"
    aid, path, is_wav, nbytes = _save_upload(f)
    out = {"audio_id": aid, "mode": mode, "asr_mode": asr_mode, "denoise": bool(want_denoise),
           "bytes": nbytes, "is_wav": is_wav, "engine": asr_engine.status(), "metrics": None,
           "metrics_dn": None, "branches": [], "text": None, "confidence": 0.0,
           "chosen": None, "note": "", "grammar": None, "current_key": current_key,
           "requires_confirmation": False, "branch_conflict": False}
    if not is_wav:
        out["note"] = "收到的不是 WAV：只做原始留档，无法分析/识别"
        return out

    try:
        p16, x = _prep_16k(path, aid)
    except (ValueError, EOFError, OSError, audio.wave.Error) as exc:
        out["error"] = "无法读取录音: %s" % exc
        return out
    m = audio.analyze(audio.TARGET_SR, x)
    out["metrics"] = m
    if m["duration"] < 0.3:
        out["note"] = "录音太短（<0.3 s），请点开始后说完一整项再点结束"
        return out
    if m["rms_dbfs"] < -60:
        out["note"] = "录音接近静音，请检查麦克风后重说"
        return out

    # 只给术语上下文，不向识别器提供 nominal、公差或实验脚本答案。
    asr_engine.set_context(schema, current_key)
    capabilities = asr_engine.get_engine().info()
    if (asr_mode == "dictation" or schema is None or
            capabilities.get("supports_grammar") is False):
        out["grammar"] = asr_engine.set_dictation()
        out["grammar"]["supported"] = capabilities.get("supports_grammar", True)
        out["asr_mode"] = "dictation"
    else:
        out["grammar"] = ensure_grammar(schema, current_key)
    out["engine"] = asr_engine.status()

    r1 = asr_engine.transcribe(p16)
    out["branches"].append({"name": "原声", "text": r1.get("text"), "confidence": r1.get("confidence"),
                            "ms": r1.get("elapsed_ms"), "error": r1.get("error")})
    best, best_name = r1, "原声"
    values1 = _branch_values(r1, schema, current_key)

    if want_denoise:
        y, info = audio.denoise(audio.TARGET_SR, x)
        pdn = os.path.join(db.AUDIO_DIR, aid.replace(".wav", "") + "_dn.wav")
        audio.write_wav(pdn, audio.TARGET_SR, y)
        out["metrics_dn"] = audio.analyze(audio.TARGET_SR, y)
        out["denoise_info"] = info
        r2 = asr_engine.transcribe(pdn)
        out["branches"].append({"name": "降噪", "text": r2.get("text"), "confidence": r2.get("confidence"),
                                "ms": r2.get("elapsed_ms"), "error": r2.get("error")})
        values2 = _branch_values(r2, schema, current_key)
        out["branch_conflict"] = bool(values1 and values2 and values1 != values2)
        if (r2.get("text") or "") and (not (r1.get("text") or "")
                                       or (values2 and not values1)
                                       or (bool(values1) == bool(values2) and
                                           (r2.get("confidence") or 0) > (r1.get("confidence") or 0))):
            best, best_name = r2, "降噪"

    out["chosen"] = best_name
    out["text"] = best.get("text")
    out["confidence"] = best.get("confidence") or 0.0
    out["confidence_kind"] = best.get("confidence_kind", "engine_score_uncalibrated")
    out["requires_confirmation"] = bool(best.get("requires_confirmation") or
                                         out["confidence"] < 0.75 or out["branch_conflict"] or
                                         m.get("clipping_pct", 0) >= 1.0)
    if out["requires_confirmation"]:
        out["confidence"] = min(out["confidence"], 0.74)
    out["elapsed_ms"] = best.get("elapsed_ms") or 0
    if not best.get("text") and best.get("error"):
        out["note"] = "识别引擎不可用：%s" % best.get("error")
    elif out["text"] == "":
        out["note"] = "引擎没听出内容，请靠近手机 10–15 cm 重说一次"
    elif out["branch_conflict"]:
        out["note"] = "原声与降噪的字段读数不同，已标记待确认，请回听或重录"
    elif m.get("clipping_pct", 0) >= 1.0:
        out["note"] = "录音削波较多，读数需确认；请稍微远离麦克风后重录"
    return out


@app.route("/api/asr/audio", methods=["POST"])
@need_login
def api_audio():
    want_dn = request.form.get("denoise") in ("1", "true", "on")
    schema = None
    tid = request.form.get("task_id")
    if tid:
        t = db.row(conn(), "SELECT * FROM tasks WHERE id=?", (tid,))
        if t:
            schema = tpl_of(t["template_id"])["schema"]
    res = _process_audio(want_dn, schema)
    if res.get("error"):
        return jsonify(res), 400
    db.audit(conn(), me(), "asr_upload", res.get("audio_id", ""),
             {"mode": res.get("mode"), "denoise": res.get("denoise"),
              "asr_mode": (res.get("grammar") or {}).get("mode"),
              "snr_db": (res.get("metrics") or {}).get("snr_db"),
              "engine": (res.get("engine") or {}).get("engine"),
              "text": (res.get("text") or "")[:60]})
    return jsonify(res)


@app.route("/api/asr/lab/script")
@need_login
def api_lab_script():
    return jsonify({"script": lab.LAB_SCRIPT, "engine": asr_engine.status(),
                    "template": {"code": seed_mod.TPL_IPQC["code"],
                                 "revision": seed_mod.TPL_IPQC["revision"]}})


@app.route("/api/asr/lab/sample", methods=["POST"])
@need_login
def api_lab_sample():
    sid = request.form.get("script_id") or ""
    want_dn = request.form.get("denoise") in ("1", "true", "on")
    res = _process_audio(want_dn, seed_mod.TPL_IPQC)
    if res.get("error"):
        return jsonify(res), 400
    judged = lab.judge_sample(sid, res.get("text") or "")
    item = next((s for s in lab.LAB_SCRIPT if s["id"] == sid), {})
    g = res.get("grammar") or {}
    c = conn()
    db.insert(c, "asr_samples", {
        "at": db.now(), "user_id": me()["id"], "script_id": sid,
        "mode": res.get("mode"), "denoise": 1 if res.get("denoise") else 0,
        "engine": (res.get("engine") or {}).get("engine"),
        "asr_mode": g.get("mode") or res.get("asr_mode"),
        "grammar_ok": 1 if g.get("ok") else 0,
        "grammar_error": (g.get("error") or "")[:200],
        "expected": str(item.get("say", "")), "text": res.get("text") or "",
        "confidence": res.get("confidence") or 0,
        "snr_db": (res.get("metrics") or {}).get("snr_db"),
        "duration": (res.get("metrics") or {}).get("duration"),
        "cer": judged.get("cer"), "ok": 1 if judged.get("ok") else 0,
        "note": (res.get("note") or "")[:200],
    })
    c.commit()
    return jsonify({"result": res, "judge": judged, "script": item})


@app.route("/api/asr/lab/summary")
@need_login
def api_lab_summary():
    c = conn()
    actor = me()
    base = " FROM asr_samples" if actor["role"]=="admin" else " FROM asr_samples WHERE user_id=?"
    params = () if actor["role"]=="admin" else (actor["id"],)
    rows = db.rows(c, "SELECT mode, denoise, asr_mode, grammar_ok, COUNT(*) n, SUM(ok) ok, AVG(cer) cer, AVG(snr_db) snr, AVG(duration) dur, AVG(confidence) conf" +
                   base + " GROUP BY mode, denoise, asr_mode, grammar_ok ORDER BY n DESC", params)
    total = db.row(c, "SELECT COUNT(*) n, SUM(ok) ok, AVG(cer) cer, AVG(snr_db) snr, SUM(CASE WHEN grammar_ok=0 THEN 1 ELSE 0 END) grammar_failed"+base,params)
    by_id = db.rows(c,"SELECT script_id, COUNT(*) n, SUM(ok) ok, AVG(cer) cer"+base+" GROUP BY script_id ORDER BY script_id",params)
    recent = db.rows(c,"SELECT *"+base+" ORDER BY id DESC LIMIT 20",params)
    n = total["n"] or 0
    return jsonify({
        "total": n,
        "ok": total.get("ok") or 0,
        "field_accuracy": round((total.get("ok") or 0) / float(n), 3) if n else None,
        "cer": round(total.get("cer"), 3) if total.get("cer") is not None else None,
        "snr_db": round(total.get("snr"), 1) if total.get("snr") is not None else None,
        "grammar_failed": total["grammar_failed"] or 0,
        "groups": rows, "by_script": by_id, "recent": recent,
        "engine": asr_engine.status(),
        "version": APP_VERSION,
    })


@app.route("/api/asr/lab/reset", methods=["POST"])
@need_login
def api_lab_reset():
    c = conn()
    c.execute("DELETE FROM asr_samples WHERE user_id=?", (me()["id"],))
    c.commit()
    db.audit(c, me(), "asr_lab_reset", "asr_samples")
    return jsonify({"ok": True})


@app.route("/api/audio/<path:name>")
@need_login
def api_audio_file(name):
    match = re.fullmatch(r"([a-f0-9]{12})(?:_16k(?:_dn)?|_dn)?\.wav",name)
    if not match: abort(404)
    actor = me()
    asset = db.row(conn(),"SELECT * FROM audio_assets WHERE name=?", (match[1]+".wav",))
    if actor["role"]!="admin":
        if not asset: abort(404)
        allowed=asset["owner_id"]==actor["id"] or access.can_steel(conn(),actor,asset["steel_id"])
        if not allowed and asset["task_id"] and actor["role"]=="supervisor":
            task=db.row(conn(),"SELECT plan_id FROM tasks WHERE id=?",(asset["task_id"],))
            allowed=task and access.can_plan(conn(),actor,task["plan_id"])
        if not allowed: abort(403,"无权读取此录音")
    response=send_from_directory(db.AUDIO_DIR,name,mimetype="audio/wav")
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["Cache-Control"]="private, no-store"
    return response


# --------------------------------------------------------------------------
# 计划 / 签字 / 出报告
# --------------------------------------------------------------------------
@app.route("/api/plans/<int:pid>")
@need_login
def api_plan(pid):
    pv = plan_view(pid)
    if not pv:
        return jsonify({"error": "计划不存在"}), 404
    return jsonify(pv)


@app.route("/api/plans/<int:pid>/sign", methods=["POST"])
@need_role("supervisor")
def api_sign(pid):
    u = me()
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    pv = plan_view(pid)
    if not pv:
        return jsonify({"error": "计划不存在"}), 404
    if pv["progress"]["passed"] < pv["progress"]["total"]:
        return jsonify({"error": "还有 %d 道工序未合格，不能复核签字"
                        % (pv["progress"]["total"] - pv["progress"]["passed"])}), 400
    body = request.get_json(force=True, silent=True) or {}
    if not pv["can_sign"]: abort(409,"本计划已经签字")
    db.insert(c, "signoffs", {
        "plan_id": pid, "role": "supervisor", "user_id": u["id"], "name": u["name"],
        "at": db.now(), "note": body.get("note") or "现场记录齐套，同意归档",
    })
    c.commit()
    db.audit(c, u, "sign_plan", "plan:%d" % pid)
    for insp in db.rows(c,"SELECT DISTINCT assignee_id AS id FROM tasks WHERE plan_id=? AND assignee_id IS NOT NULL",(pid,)):
        db.notify(c, insp["id"], "sign", "复核已通过", "室主任 %s 已对产品记录签字" % u["name"])
    return jsonify(plan_view(pid))


@app.route("/api/plans/<int:pid>/submit", methods=["POST"])
@need_login
def api_submit(pid):
    u = me()
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    pv = plan_view(pid)
    if not pv:
        return jsonify({"error": "计划不存在"}), 404
    if pv["progress"]["passed"] < pv["progress"]["total"]:
        return jsonify({"error": "未齐套：还有工序未判定合格"}), 400
    if not any(s["role"] == "supervisor" for s in pv["signoffs"]):
        return jsonify({"error": "缺室主任复核签字"}), 400
    if pv.get("report"):
        return jsonify({"error": "该计划已生成过报告（%s），如需重出请先作废原报告"
                        % pv["report"].get("file")}), 400

    bundle = report_bundle(pid)
    n = len(db.rows(c, "SELECT id FROM reports WHERE plan_id=?", (pid,)))
    report_no = "RPT-%s-%02d" % (pv["plan"]["code"].replace("ITP-", ""), n + 1)
    bundle["report_no"] = report_no
    fname = "%s.pdf" % report_no
    path = os.path.join(db.REPORT_DIR, fname)
    _path, pages = report_mod.build_report(bundle, path)
    rid = db.insert(c, "reports", {
        "plan_id": pid, "file": fname, "created_at": db.now(), "pages": pages,
        "snapshot": bundle["snapshot"],
    })
    c.execute("UPDATE plans SET status='closed' WHERE id=?", (pid,))
    c.commit()
    db.audit(c, u, "generate_report", "plan:%d" % pid, {"report_no": report_no, "pages": pages})
    db.notify(c, u["id"], "report", "检验记录已生成", "%s · 共 %d 页" % (report_no, pages))
    return jsonify({"report_id": rid, "report_no": report_no, "pages": pages,
                    "url": "/api/reports/%d/pdf" % rid})


@app.route("/api/reports/<int:rid>/pdf")
@need_login
def api_report_pdf(rid):
    r = db.row(conn(), "SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        return jsonify({"error": "报告不存在"}), 404
    path = os.path.join(db.REPORT_DIR, r["file"])
    if not os.path.exists(path):
        return jsonify({"error": "文件已丢失"}), 404
    from inspect import signature
    filename_arg = "download_name" if "download_name" in signature(send_file).parameters else "attachment_filename"
    return send_file(path, mimetype="application/pdf", as_attachment=False, **{filename_arg: r["file"]})


@app.route("/api/reports/<int:rid>/preview.png")
@need_login
def api_report_preview(rid):
    r = db.row(conn(), "SELECT * FROM reports WHERE id=?", (rid,))
    if not r:
        return jsonify({"error": "报告不存在"}), 404
    page = int(request.args.get("page", 0))
    bundle = report_bundle(r["plan_id"])
    bundle["report_no"] = os.path.splitext(r["file"])[0]
    png, _pages = report_mod.preview_png(bundle, page_index=page)
    return make_response(png, 200, {"Content-Type": "image/png", "Cache-Control": "no-store"})


# --------------------------------------------------------------------------
# 模板（工艺维护）
# --------------------------------------------------------------------------
@app.route("/api/templates")
@need_login
def api_templates():
    rs = db.rows(conn(), "SELECT id, code, revision, title, std_version, created_at, active "
                         "FROM form_templates ORDER BY code, id DESC")
    return jsonify({"templates": rs})


@app.route("/api/templates/<int:tid>")
@need_login
def api_template(tid):
    t = tpl_of(tid)
    if not t:
        return jsonify({"error": "模板不存在"}), 404
    used = db.rows(conn(), "SELECT t.id,t.plan_id, t.seq, p.code AS plan_code FROM tasks t "
                           "JOIN plans p ON p.id=t.plan_id WHERE t.template_id=?", (tid,))
    used = [r for r in used if access.can_plan(conn(),me(),r["plan_id"])]
    return jsonify({"template": {"id": t["id"], "code": t["code"], "revision": t["revision"],
                                 "title": t["title"], "std_version": t["std_version"],
                                 "spec_ref": t["spec_ref"], "schema": t["schema"]},
                    "used_by": used})


@app.route("/api/templates/<int:tid>/publish", methods=["POST"])
@need_role("process")
def api_publish(tid):
    u = me()
    body = request.get_json(force=True, silent=True) or {}
    schema = body.get("schema")
    revision = (body.get("revision") or "").strip()
    if not schema or not schema.get("sections"):
        return jsonify({"error": "模板结构为空"}), 400
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    old = db.row(c, "SELECT * FROM form_templates WHERE id=?", (tid,))
    if not old:
        return jsonify({"error": "模板不存在"}), 404
    if not old["active"]: abort(409,"该版本已被替换，请打开最新版本")
    for field in judge_mod.flatten_fields(schema):
        for key in ("min","max","tol","nominal"):
            val=field.get(key)
            if val is not None and (type(val) not in (int,float) or not math.isfinite(val)):
                abort(400,"模板上下限、公差和公称值须为有限数值")
        if field.get("tol",0) is not None and field.get("tol",0)<0: abort(400,"公差不能为负")
        if field.get("min") is not None and field.get("max") is not None and field["min"]>field["max"]: abort(400,"最小值不能大于最大值")
    if not revision:
        try:
            cur = old["revision"].lstrip("v")
            major, _, minor = cur.partition(".")
            revision = "v%s.%d" % (major or "2", int(minor or 0) + 1)
        except Exception:
            revision = old["revision"] + ".1"
    if db.row(c,"SELECT id FROM form_templates WHERE code=? AND revision=?",(old["code"],revision)):
        abort(409,"该规格版本已存在")
    schema["revision"] = revision
    schema["code"] = old["code"]
    c.execute("UPDATE form_templates SET active=0 WHERE code=?", (old["code"],))
    new_id = db.insert(c, "form_templates", {
        "code": old["code"], "revision": revision, "title": schema.get("title") or old["title"],
        "std_version": schema.get("std_version") or old["std_version"],
        "spec_ref": schema.get("spec_ref") or old["spec_ref"],
        "schema_json": json.dumps(schema, ensure_ascii=False), "created_at": db.now(), "active": 1,
    })
    c.commit()
    db.audit(c, u, "publish_template", "template:%s" % old["code"],
             {"from": old["revision"], "to": revision})
    return jsonify({"template_id": new_id, "revision": revision,
                    "message": "已发布 %s %s，新任务将使用新版本；进行中任务保留原快照" % (old["code"], revision)})


@app.route("/api/tasks/<int:tid>/sync-template", methods=["POST"])
@need_role("admin")
def api_sync_template(tid):
    u = me()
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    t = db.row(c, "SELECT * FROM tasks WHERE id=?", (tid,))
    if not t:
        return jsonify({"error": "任务不存在"}), 404
    if t["status"]!="open" or t["assignee_id"] is not None:
        abort(409,"模板只能在任务领取前更新")
    cur = tpl_of(t["template_id"])
    latest = db.row(c, "SELECT * FROM form_templates WHERE code=? AND active=1 ORDER BY id DESC",
                    (cur["code"],))
    if not latest:
        return jsonify({"error": "没有可用版本"}), 400
    if latest["id"] == t["template_id"]:
        return jsonify({"ok": True, "message": "已是当前版本 %s" % latest["revision"]})
    c.execute("UPDATE tasks SET template_id=?, template_revision=?,version=version+1 WHERE id=? AND status='open' AND assignee_id IS NULL",
              (latest["id"], latest["revision"], tid))
    c.commit()
    db.audit(c, u, "sync_template", "task:%d" % tid,
             {"from": cur["revision"], "to": latest["revision"]})
    return jsonify({"ok": True, "revision": latest["revision"],
                    "message": "该任务已升级到模板 %s（历史记录仍按旧版本渲染）" % latest["revision"]})


# --------------------------------------------------------------------------
# 留痕 / 通知 / 演示重置
# --------------------------------------------------------------------------
@app.route("/api/audit")
@need_login
def api_audit():
    target = request.args.get("target")
    rs = access.audit_rows(conn(),me(),target)
    return jsonify({"audits": rs})


@app.route("/api/notifications")
@need_login
def api_notifications():
    rs = db.rows(conn(), "SELECT * FROM notifications WHERE user_id=? ORDER BY id DESC",
                 (me()["id"],))
    return jsonify({"notifications": rs, "unread": sum(1 for r in rs if not r["read"])})


@app.route("/api/notifications/read", methods=["POST"])
@need_login
def api_notifications_read():
    c = conn()
    c.execute("UPDATE notifications SET read=1 WHERE user_id=?", (me()["id"],))
    c.commit()
    return jsonify({"ok": True})


# The enterprise service never exposes a database reset over HTTP.


def _serve(application, host, port, use_https, cert_dir):
    """优先用 waitress（装了就用），否则回退 Flask 开发服务器。https 必须走 Flask。"""
    if not use_https:
        try:
            from waitress import serve
            print("[note] WSGI 服务器: waitress（多线程，生产可用）")
            serve(application, host=host, port=port, threads=12)
            return
        except ImportError:
            pass
    print("[note] WSGI 服务器: Flask 开发服务器 —— 演示够用；")
    print("       下面出现两条 'development server' 红字是它的固定提示，不是错误。")
    print("       想换成生产级：pip install waitress 后重启即可（会自动启用）。")
    if use_https:
        application.run(host=host, port=port, threaded=True, ssl_context=(
            os.path.join(cert_dir, "cert.pem"), os.path.join(cert_dir, "key.pem")))
    else:
        application.run(host=host, port=port, threaded=True)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="质检采集 Demo 服务")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8760)
    ap.add_argument("--https", action="store_true", help="启用自签 HTTPS（手机调用麦克风需要）")
    args = ap.parse_args()

    # Cookies are scoped to the host, not the port. A phone can keep its
    # current session when opening another local demo instance for downloads.
    app.config["SESSION_COOKIE_NAME"] = "qc_demo_%d" % args.port
    app.config["SESSION_COOKIE_SECURE"] = args.https
    db.init_db()
    c = db.connect()
    created = seed_mod.seed_if_empty(c)
    c.close()
    print("[init] database:", db.DB_PATH, "seeded" if created else "already seeded")
    st = asr_engine.status()          # 启动时就把识别引擎拉起来，避免首个请求卡住
    print("[init] asr engine:", st.get("engine"),
          "ready" if st.get("available") else ("unavailable -> " + str(st.get("reason"))))
    print("[run ] http%s://%s:%d" % ("s" if args.https else "", args.host, args.port))
    _serve(app, args.host, args.port, args.https, os.path.join(ROOT, "certs"))


steel_routes.register(app, conn, me, need_login, _process_audio)
identity.register(app, conn)
workflow.register(app,conn,me,need_role,task_view)
management.register(app, conn, me, need_role)
management_cases.register(app, conn, me, need_role)
management_ai.register(app, conn, me, need_role)
steel_recovery.register(app, conn, me, need_login)
microphone_help.register(app, ROOT, conn, me, need_login)

if __name__ == "__main__":
    main()
