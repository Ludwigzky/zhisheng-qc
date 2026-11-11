"""Explicit assignment and sequencing of inspection tasks."""
import json
from flask import Blueprint, abort, jsonify, request
import db
import seed


def init_schema(c):
    for table, column, definition in (
        ("plans", "workgroup", "TEXT NOT NULL DEFAULT ''"),
        ("tasks", "version", "INTEGER NOT NULL DEFAULT 1"),
        ("tasks", "predecessors", "TEXT")):
        if column not in {r[1] for r in c.execute("PRAGMA table_info("+table+")")}:
            c.execute("ALTER TABLE "+table+" ADD COLUMN "+column+" "+definition)
    c.execute("""CREATE TABLE IF NOT EXISTS audio_assets
                 (name TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, steel_id INTEGER, task_id INTEGER)""")


def predecessors(task):
    if task["predecessors"] is None:
        return [task["seq"]-1] if task["seq"] > 1 else []
    return json.loads(task["predecessors"])


def check_prerequisites(c, task):
    states = {r["seq"]: r["status"] for r in db.rows(c, "SELECT seq,status FROM tasks WHERE plan_id=?", (task["plan_id"],))}
    return [n for n in predecessors(task) if states.get(n) != "pass"]


def begin_write(c, tid, body):
    c.execute("BEGIN IMMEDIATE")
    task = db.row(c, "SELECT * FROM tasks WHERE id=?", (tid,))
    if not task:
        abort(404)
    if not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] != task["version"]:
        abort(409, "任务已更新，请重新打开后操作")
    if task["status"] == "pass" or db.row(c,"SELECT status FROM plans WHERE id=?",(task["plan_id"],))["status"] == "closed":
        abort(409, "工序已完成或计划已归档")
    if check_prerequisites(c, task):
        abort(409, "请先完成前置工序")
    return task


def record_event(c, actor, action, target, details):
    db.insert(c, "audit", dict(at=db.now(), actor_id=actor["id"], actor_name=actor["name"],
                               action=action, target=target, detail=json.dumps(details, ensure_ascii=False)))


def register(app, conn, me, need_role, task_view):
    bp = Blueprint("work", __name__)

    @bp.route("/api/tasks/<int:tid>/claim", methods=["POST"])
    @need_role("inspector")
    def claim(tid):
        c, actor = conn(), me()
        with c:
            task = begin_write(c, tid, request.get_json(silent=True))
            if task["assignee_id"] is not None:
                abort(409, "任务已有人领取，请使用交接")
            c.execute("UPDATE tasks SET status='doing', assignee_id=?, claimed_at=?, version=version+1 WHERE id=?",
                      (actor["id"], db.now(), tid))
            record_event(c, actor, "claim_task", "task:%d"%tid, {})
        return jsonify(task=task_view(db.row(c,"SELECT * FROM tasks WHERE id=?",(tid,))))

    @bp.route("/api/tasks/<int:tid>/handoff", methods=["POST"])
    @need_role("inspector","supervisor")
    def handoff(tid):
        c, actor = conn(), me()
        body = request.get_json(silent=True)
        with c:
            task = begin_write(c, tid, body)
            if not task["assignee_id"] or (actor["role"]=="inspector" and task["assignee_id"]!=actor["id"]):
                abort(403,"仅领取人或本组室主任可以交接")
            if type(body.get("recipient")) is not int:
                abort(400,"请选择接收人")
            recipient = db.row(c, """SELECT u.id FROM users u JOIN user_credentials a ON a.user_id=u.id
                WHERE u.id=? AND u.role='inspector' AND u.dept=? AND a.enabled=1""", (body["recipient"],actor["dept"]))
            if not recipient or recipient["id"]==task["assignee_id"]:
                abort(400,"请选择本组另一名在用质量工程师")
            c.execute("UPDATE tasks SET assignee_id=?,version=version+1 WHERE id=?", (recipient["id"],tid))
            record_event(c,actor,"task_handoff","task:%d"%tid,dict(previous=task["assignee_id"],recipient=recipient["id"]))
        return jsonify(ok=True)

    def read_config(body, sequences):
        if not isinstance(body, dict):
            abort(400,"请填写班组和工序设置")
        group = (body.get("workgroup") or "").strip() if isinstance(body.get("workgroup"),str) else ""
        dependencies = body.get("predecessors")
        if not group or len(group)>80 or not isinstance(dependencies,dict):
            abort(400,"班组和前置工序设置不完整")
        for n in sequences:
            values=dependencies.get(str(n))
            if not isinstance(values,list) or any(type(x) is not int or x not in sequences or x>=n for x in values):
                abort(400,"前置工序只能选择序号更小的节点")
        return group, dependencies

    @bp.route("/api/plans/<int:pid>/workflow", methods=["POST"])
    @need_role("admin")
    def configure(pid):
        c=conn()
        body=request.get_json(silent=True)
        with c:
            c.execute("BEGIN IMMEDIATE")
            tasks=db.rows(c,"SELECT id,seq,status,assignee_id FROM tasks WHERE plan_id=? ORDER BY seq",(pid,))
            plan=db.row(c,"SELECT * FROM plans WHERE id=?",(pid,))
            if not tasks or not plan: abort(404)
            group, deps=read_config(body,{t["seq"] for t in tasks})
            if plan["status"]=="closed" or any(t["assignee_id"] or t["status"]!="open" for t in tasks):
                abort(409,"已领用的计划不能改组或调整前置工序，请新建计划")
            c.execute("UPDATE plans SET workgroup=? WHERE id=?",(group,pid))
            for t in tasks:
                c.execute("UPDATE tasks SET predecessors=?,version=version+1 WHERE id=?",(json.dumps(sorted(set(deps[str(t["seq"])]))),t["id"]))
            record_event(c,me(),"configure_plan","plan:%d"%pid,{"workgroup":group,"predecessors":deps})
        return jsonify(ok=True)

    @bp.route("/api/work/plans", methods=["POST"])
    @need_role("admin")
    def create_plan():
        body=request.get_json(silent=True)
        group,deps=read_config(body,{n["seq"] for n in seed.PLAN_NODES})
        code=body.get("code"); name=body.get("name")
        if not all(isinstance(v,str) and 0<len(v.strip())<=80 for v in (code,name)):
            abort(400,"请填写产品工号和名称")
        import re
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", code): abort(400,"产品工号仅支持字母、数字、下划线或短横线")
        c=conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            if db.row(c,"SELECT id FROM products WHERE code=?",(code,)): abort(409,"该工号已存在")
            product=db.insert(c,"products",dict(code=code,name=name.strip(),model="",customer="",contract_no="",note=""))
            pid=db.insert(c,"plans",dict(product_id=product,code="ITP-"+code,status="open",std_ref="",created_at=db.now(),workgroup=group))
            for n in seed.PLAN_NODES:
                tpl=db.row(c,"SELECT * FROM form_templates WHERE code=? AND active=1 ORDER BY id DESC",(n["template"]["code"],))
                if not tpl: abort(409,"缺少已发布的工序模板")
                node=db.insert(c,"plan_nodes",dict(plan_id=pid,seq=n["seq"],name=n["name"],short_name=n["short_name"],template_id=tpl["id"]))
                db.insert(c,"tasks",dict(plan_id=pid,node_id=node,seq=n["seq"],template_id=tpl["id"],template_revision=tpl["revision"],
                         status="open",predecessors=json.dumps(sorted(set(deps[str(n["seq"])])))))
            record_event(c,me(),"create_plan","plan:%d"%pid,{"workgroup":group})
        return jsonify(plan_id=pid),201
    app.register_blueprint(bp)
