"""Append-only management follow-up of failed inspections.

Following an exception does not replace a measurement or a release decision.
The original inspection remains immutable and is checked on every operation.
"""
import json
import math
from datetime import date, datetime, timedelta, timezone

from flask import Blueprint, abort, jsonify, request

import access
import db


STATUSES = ("open", "following", "closed")


def init_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS management_cases (
        kind TEXT NOT NULL CHECK(kind IN ('steel','task')),
        record_id INTEGER NOT NULL, version INTEGER NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('open','following','closed')),
        assignee_id INTEGER REFERENCES users(id), due_date TEXT,
        note TEXT NOT NULL, updated_at TEXT NOT NULL,
        updated_by INTEGER NOT NULL REFERENCES users(id),
        PRIMARY KEY(kind,record_id))""")
    c.execute("""CREATE TABLE IF NOT EXISTS management_case_events (
        id INTEGER PRIMARY KEY, kind TEXT NOT NULL, record_id INTEGER NOT NULL,
        version INTEGER NOT NULL, at TEXT NOT NULL,
        actor_id INTEGER NOT NULL REFERENCES users(id), actor_name TEXT NOT NULL,
        action TEXT NOT NULL, status TEXT NOT NULL,
        assignee_id INTEGER REFERENCES users(id), assignee_name TEXT,
        due_date TEXT, note TEXT NOT NULL,
        UNIQUE(kind,record_id,version))""")
    c.execute("CREATE INDEX IF NOT EXISTS management_cases_due ON management_cases(status,due_date)")
    c.execute("CREATE INDEX IF NOT EXISTS management_case_events_record ON management_case_events(kind,record_id,id)")


def _public(item):
    return {key: value for key, value in item.items() if not key.startswith("_")}


def _ancestors(record, by_id):
    """Return visible ancestors, tolerating incomplete historical chains."""
    seen = {record["id"]}
    result = []
    parent = record.get("parent_id")
    while parent is not None and parent in by_id and parent not in seen:
        seen.add(parent)
        result.append(parent)
        parent = by_id[parent].get("parent_id")
    return result


def _followups(records):
    result = {}
    by_id = {r["id"]: r for r in records if r["kind"] == "steel"}
    for child in by_id.values():
        signed = child.get("_raw", {}).get("signed_at") and child.get("_raw", {}).get("inspector_name")
        passed = bool(child["state"] == "pass" and child.get("status") == "final" and signed)
        for parent in _ancestors(child, by_id):
            previous_id, previous_pass = result.get(("steel", parent), (child["id"], False))
            result[("steel", parent)] = min(previous_id, child["id"]), previous_pass or passed
    tasks = {}
    for record in records:
        if record["kind"] == "task":
            task_id = record.get("task_id", record.get("_raw", {}).get("task_id"))
            tasks.setdefault(task_id, []).append(record)
    for rounds in tasks.values():
        next_id, passed = None, False
        groups = {}
        for record in rounds:
            round_number = record.get("_raw", {}).get("round", record.get("round", 0))
            groups.setdefault(round_number, []).append(record)
        for round_number in sorted(groups, reverse=True):
            for record in groups[round_number]:
                result[("task", record["id"])] = next_id, passed
            next_id = min(record["id"] for record in groups[round_number])
            passed = passed or any(record["state"] == "pass" for record in groups[round_number])
    return result


def _case(row, today):
    if row is None:
        return {"version": 0, "status": "open", "assignee_id": None,
                "assignee_name": None, "due_date": None, "note": "", "updated_at": None, "overdue": False}
    value = dict(row)
    return {key: value.get(key) for key in ("version", "status", "assignee_id", "assignee_name", "due_date", "note", "updated_at")} | {
        "overdue": bool(value["status"] != "closed" and value.get("due_date") and value["due_date"] < today)}


def scoped_defects(c, actor, args=None):
    # Local import prevents a dependency cycle when the overview calls us.
    import management
    visible = management.visible_records(c, actor)
    selected = visible
    if args:
        filters = {key: value for key, value in args.items() if key not in ("state", "status", "overdue", "assignee_id")}
        selected = management.filtered_records(c, actor, filters)
    steel_scope, steel_args = access.steel_scope(actor)
    task_scope = "1" if actor["role"] == "admin" else "p.workgroup=?"
    task_args = () if actor["role"] == "admin" else (actor.get("dept") or "",)
    stored = {(r["kind"], r["record_id"]): r for r in db.rows(c, """SELECT mc.*,u.name AS assignee_name
        FROM management_cases mc LEFT JOIN users u ON u.id=mc.assignee_id
        WHERE (mc.kind='steel' AND EXISTS(SELECT 1 FROM steel_records r WHERE r.id=mc.record_id AND """ + steel_scope + """))
        OR (mc.kind='task' AND EXISTS(SELECT 1 FROM records rr JOIN tasks t ON t.id=rr.task_id
        JOIN plans p ON p.id=t.plan_id WHERE rr.id=mc.record_id AND """ + task_scope + "))", tuple(steel_args) + task_args)}
    followups = _followups(visible)
    today = datetime.now(timezone(timedelta(hours=8))).date().isoformat()
    result = []
    for record in selected:
        if record["state"] != "fail":
            continue
        item = _public(record)
        followup_id, passed = followups.get((record["kind"],record["id"]), (None,False))
        data = record.get("_data", {})
        rejected = record["kind"] == "steel" and data.get("disposition") == "拒收" and data.get("additional_failure_confirmed") is True
        item.update(followup_record_id=followup_id, has_passed_followup=passed,
                    closure_allowed=bool(passed or rejected),
                    closure_reason="后续复检已合格" if passed else "原单已正式记录拒收处置" if rejected else "需先完成后续合格复检，或在原单正式记录拒收处置",
                    case=_case(stored.get((record["kind"], record["id"])), today))
        item.setdefault("dept", record.get("_raw", {}).get("workgroup") if record["kind"] == "task" else record.get("owner", {}).get("dept", ""))
        result.append(item)
    return sorted(result, key=lambda r: (r["case"]["status"] == "closed", not r["case"]["overdue"], r.get("created_at", "")), reverse=False)


def summary_counts(items):
    return {status: sum(item["case"]["status"] == status for item in items) for status in STATUSES} | {
        "overdue": sum(item["case"]["overdue"] for item in items)}


def _record(c, actor, kind, rid):
    if kind not in ("steel", "task"):
        abort(404, "异常记录不存在")
    if kind == "steel":
        allowed = access.can_steel(c, actor, rid)
    else:
        task = db.row(c, "SELECT task_id FROM records WHERE id=?", (rid,))
        plan = db.row(c, "SELECT plan_id FROM tasks WHERE id=?", (task["task_id"],)) if task else None
        allowed = bool(plan and access.can_plan(c, actor, plan["plan_id"]))
    if not allowed:
        abort(404, "异常记录不存在")
    for item in scoped_defects(c, actor):
        if item["kind"] == kind and item["id"] == rid:
            return item
    abort(404, "该记录没有可跟进的不合格结论")


def _assignees(c, actor, item=None):
    clauses, params = ["uc.enabled=1", "u.role IN ('inspector','supervisor')"], []
    group = (item or {}).get("dept") if item else actor.get("dept")
    if actor["role"] != "admin" or item is not None:
        clauses.append("u.dept=?")
        params.append(group or "")
    rows = db.rows(c, "SELECT u.id,u.name,u.dept,u.role FROM users u JOIN user_credentials uc ON uc.user_id=u.id WHERE " +
                   " AND ".join(clauses) + " ORDER BY u.dept,u.name,u.id", tuple(params))
    if item is None:
        return rows
    # Assignment must not grant an inspector access to another employee's sheet.
    if item["kind"] == "steel":
        return [r for r in rows if r["role"] == "supervisor" or r["id"] == item["owner"]["id"]]
    task = db.row(c, "SELECT t.assignee_id FROM records r JOIN tasks t ON t.id=r.task_id WHERE r.id=?", (item["id"],))
    permitted = {item["owner"]["id"], task["assignee_id"] if task else None}
    return [r for r in rows if r["role"] == "supervisor" or r["id"] in permitted]


def _history(c, kind, rid):
    return db.rows(c, """SELECT id,version,at,actor_name,action,status,assignee_id,assignee_name,due_date,note
        FROM management_case_events WHERE kind=? AND record_id=? ORDER BY id DESC""", (kind, rid))


def _updates(body, c, actor, item):
    if not isinstance(body, dict) or type(body.get("version")) is not int or body["version"] < 0:
        abort(400, "请提交当前跟进版本")
    status = body.get("status")
    if status not in STATUSES:
        abort(400, "请选择待处理、跟进中或已关闭")
    note = body.get("note")
    if not isinstance(note, str) or not note.strip() or len(note.strip()) > 2000 or "\x00" in note:
        abort(400, "请填写本次跟进说明（最多2000字）")
    assignee = body.get("assignee_id")
    if assignee is not None and type(assignee) is not int:
        abort(400, "请选择有效责任人")
    assignees = {r["id"]: r for r in _assignees(c, actor, item)}
    if assignee is not None and assignee not in assignees:
        abort(400, "请选择能够访问此记录的本组在用质量工程师或室主任")
    due = body.get("due_date") or None
    if due is not None:
        try:
            if not isinstance(due, str) or date.fromisoformat(due).isoformat() != due:
                raise ValueError()
        except ValueError:
            abort(400, "请填写有效的跟进截止日期")
    if status == "following" and assignee is None:
        abort(400, "跟进中事项需要指定责任人")
    if status == "closed" and not item["closure_allowed"]:
        abort(409, item["closure_reason"])
    return dict(version=body["version"], status=status, note=note.strip(), assignee_id=assignee,
                assignee_name=assignees[assignee]["name"] if assignee is not None else None, due_date=due)


def register(app, conn, me, need_role):
    bp = Blueprint("management_cases", __name__)

    @bp.after_request
    def no_cache(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.route("/api/management/cases")
    @need_role("admin", "supervisor")
    def cases():
        args, actor, c = request.args, me(), conn()
        try:
            page, size = int(args.get("page", 1)), int(args.get("page_size", 20))
            if page < 1 or not 1 <= size <= 100:
                raise ValueError("分页参数无效")
            items = scoped_defects(c, actor, args)
            summary = summary_counts(items)
            status = args.get("status", args.get("state", ""))
            if status and status not in STATUSES:
                raise ValueError("异常跟进状态无效")
            if status:
                items = [r for r in items if r["case"]["status"] == status]
            overdue = args.get("overdue", "")
            if overdue not in ("", "0", "1"):
                raise ValueError("逾期筛选无效")
            if overdue:
                items = [r for r in items if r["case"]["overdue"] == (overdue == "1")]
            if args.get("assignee_id"):
                assignee = int(args["assignee_id"])
                items = [r for r in items if r["case"]["assignee_id"] == assignee]
        except (ValueError, TypeError) as error:
            return jsonify(error=str(error) or "筛选条件无效"), 400
        total = len(items)
        pages = max(1, math.ceil(total / size))
        page = min(page, pages)
        return jsonify(items=items[(page-1)*size:page*size], total=total, page=page, page_size=size,
                       pages=pages, summary=summary, assignees=_assignees(c, actor))

    @bp.route("/api/management/cases/<kind>/<int:rid>", methods=["GET", "PUT", "PATCH"])
    @need_role("admin", "supervisor")
    def case(kind, rid):
        actor, c = me(), conn()
        if request.method != "GET":
            with c:
                c.execute("BEGIN IMMEDIATE")
                item = _record(c, actor, kind, rid)
                value = _updates(request.get_json(silent=True), c, actor, item)
                if value["version"] != item["case"]["version"]:
                    abort(409, "跟进事项已被其他管理者更新，请重新打开")
                now, version = db.now(), value["version"] + 1
                if value["version"] == 0:
                    c.execute("""INSERT INTO management_cases
                        (kind,record_id,version,status,assignee_id,due_date,note,updated_at,updated_by)
                        VALUES(?,?,?,?,?,?,?,?,?)""", (kind,rid,version,value["status"],value["assignee_id"],value["due_date"],value["note"],now,actor["id"]))
                else:
                    changed = c.execute("""UPDATE management_cases SET version=?,status=?,assignee_id=?,due_date=?,note=?,updated_at=?,updated_by=?
                        WHERE kind=? AND record_id=? AND version=?""", (version,value["status"],value["assignee_id"],value["due_date"],value["note"],now,actor["id"],kind,rid,value["version"]))
                    if changed.rowcount != 1:
                        abort(409, "跟进事项已更新，请重新打开")
                event = dict(kind=kind,record_id=rid,version=version,at=now,actor_id=actor["id"],actor_name=actor["name"],
                             action="case_created" if version == 1 else "case_updated",status=value["status"],
                             assignee_id=value["assignee_id"],assignee_name=value["assignee_name"],due_date=value["due_date"],note=value["note"])
                db.insert(c, "management_case_events", event)
                if value["assignee_id"] is not None and value["assignee_id"] != actor["id"] and value["assignee_id"] != item["case"]["assignee_id"]:
                    db.insert(c, "notifications", dict(user_id=value["assignee_id"],kind="quality_followup",
                        title="检验异常已分派给你跟进",body="%s · %s%s。%s" % (
                            item.get("document_no") or item["key"],actor["name"],
                            " · 截止 "+value["due_date"] if value["due_date"] else "",value["note"]),
                        read=0,created_at=now))
                db.insert(c, "audit", dict(at=now,actor_id=actor["id"],actor_name=actor["name"],action="management_case_update",
                          target=("steel:%d" if kind == "steel" else "record:%d") % rid,
                          detail=json.dumps({"version":version,"status":value["status"],"assignee_id":value["assignee_id"],"due_date":value["due_date"],"note":value["note"]},ensure_ascii=False)))
        item = _record(c, actor, kind, rid)
        return jsonify(item=item, history=_history(c, kind, rid), assignees=_assignees(c, actor, item))

    app.register_blueprint(bp)
