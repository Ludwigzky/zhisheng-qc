"""Scoped inspection ledgers, historical detail and management statistics.

All counters are calculated from the authorized source records before pagination.
Steel drafts and generic pending submissions never enter the pass-rate denominator.
"""
import csv
from collections import Counter
from datetime import date
import io
import json
import math

from flask import Blueprint, Response, abort, jsonify, request

import access
import db
import steel
import steel_standards


KINDS = {"steel": "钢板进货检验", "task": "工序检验"}
STATES = {"draft", "pass", "fail", "pending"}
STATE_FILTERS = STATES | {"final", "unfinished", "first", "retest"}
ACTION_LABELS = {
    "steel_create": "建立检验草稿", "steel_save": "保存草稿", "steel_finalize": "确认检验记录",
    "steel_followup": "建立后续检验", "steel_followup_reuse": "继续后续检验",
    "steel_draft_takeover": "接管草稿", "steel_standard_bind": "绑定工艺标准",
    "steel_deviation_request": "申请临时偏差", "steel_deviation_decision": "审批临时偏差",
    "steel_asr": "语音录入", "steel_export": "读取正式 Excel", "steel_download_link": "申请下载链接",
    "claim_task": "领取任务", "task_handoff": "交接任务", "submit_record": "提交测量结果",
    "sync_template": "更新任务模板", "sign_plan": "室主任复核", "generate_report": "归档计划报告",
}


def _json(value, fallback):
    try:
        return json.loads(value, parse_constant=lambda _: None) if value else fallback
    except (TypeError, ValueError):
        return fallback


def _owner(row):
    uid = row.get("person_id") or row.get("owner_id") or row.get("operator_id")
    return {"id": uid, "name": row.get("person_name") or row.get("inspector_name") or "历史人员",
            "dept": row.get("person_dept") or ""}


def _steel_records(c, actor):
    scope, bindings = access.steel_scope(actor, "r")
    rows = db.rows(c, """SELECT r.*, u.id AS person_id,u.name AS person_name,u.dept AS person_dept,
        s.code AS standard_code,s.revision AS standard_revision,s.source AS standard_source,s.criteria AS standard_criteria,
        a.xlsx_sha256 AS archive_sha256,a.archived_at AS archived_at
        FROM steel_records r LEFT JOIN users u ON u.id=r.owner_id
        LEFT JOIN steel_standards s ON s.id=r.standard_id
        LEFT JOIN steel_archives a ON a.record_id=r.id WHERE """ + scope, bindings)
    output = []
    for row in rows:
        data = _json(row["data_json"], {})
        if data.get("is_example"):
            continue
        evaluation = _json(row["evaluation_json"], {})
        final = row["status"] == "final" and bool(row.get("signed_at") and row.get("inspector_name"))
        state = evaluation.get("overall") if final else ("pending" if row["status"] == "final" else "draft")
        if state not in STATES:
            state = "pending"
        standard = {"id": row.get("standard_id"), "code": row.get("standard_code") or steel.RECORD_CODE,
                    "title": row.get("standard_source") or "历史记录标准",
                    "version": str(row["standard_revision"]) if row.get("standard_revision") is not None else "历史版本",
                    "criteria": _json(row.get("standard_criteria"), {})}
        output.append({
            "key": "steel:%d" % row["id"], "id": row["id"], "kind": "steel", "title": KINDS["steel"],
            "document_no": data.get("document_no") or "", "product_name": data.get("product_name") or "",
            "specification": data.get("specification") or "", "grade": data.get("grade") or "",
            "manufacturer": data.get("manufacturer") or "", "heat_no": data.get("heat_no") or "",
            "inspection_date": data.get("inspection_date") or row["created_at"][:10],
            "created_at": row["created_at"], "updated_at": row["updated_at"], "signed_at": row.get("signed_at"),
            "owner": _owner(row), "dept": row.get("person_dept") or "", "state": state,
            "overall": evaluation.get("overall") or "pending",
            "status": row["status"], "parent_id": row["parent_id"], "root_id": row["id"],
            "standard": standard, "version": row["version"], "snapshot": row["snapshot"],
            "issue_count": len(evaluation.get("issues") or []),
            "source_url": "#/steel/%d" % row["id"],
            "preview_url": "/api/steel/records/%d/preview" % row["id"],
            "download_url": "/api/steel/records/%d/xlsx" % row["id"] if final else None,
            "archive_sha256": row.get("archive_sha256"), "archived_at": row.get("archived_at"),
            "_data": data, "_evaluation": evaluation, "_raw": row,
            "_first_inspection": row["parent_id"] is None and state in ("pass", "fail"),
            "_retest": row["parent_id"] is not None and state in ("pass", "fail"),
        })
    by_id = {item["id"]: item for item in output}
    for item in output:
        current, visited = item, set()
        while current["parent_id"] in by_id and current["id"] not in visited:
            visited.add(current["id"])
            current = by_id[current["parent_id"]]
        item["root_id"] = min(visited) if current["id"] in visited else current["id"]
        # A historical transfer cannot expose a former team's record through a relation.
        if item["parent_id"] not in by_id:
            item["parent_id"] = None
            item["lineage_restricted"] = item["_raw"]["parent_id"] is not None
    return output


def _task_records(c, actor):
    if actor["role"] != "admin" and not actor.get("dept"):
        return []
    condition, params = "1", ()
    if actor["role"] != "admin":
        condition, params = "p.workgroup=?", (actor.get("dept") or "",)
    rows = db.rows(c, """SELECT r.*,u.id AS person_id,u.name AS person_name,u.dept AS person_dept,
        t.plan_id,t.latest_record_id,t.seq AS task_seq,p.code AS plan_code,p.status AS plan_status,
        p.workgroup,pr.name AS product_name,pr.code AS product_code,pr.model AS product_model,
        n.name AS node_name,f.title AS template_title,
        report.id AS report_id,report.snapshot AS report_snapshot
        FROM records r JOIN tasks t ON t.id=r.task_id JOIN plans p ON p.id=t.plan_id
        JOIN products pr ON pr.id=p.product_id LEFT JOIN users u ON u.id=r.operator_id
        LEFT JOIN plan_nodes n ON n.id=t.node_id LEFT JOIN form_templates f ON f.id=r.template_id
        LEFT JOIN reports report ON report.id=(SELECT max(last.id) FROM reports last WHERE last.plan_id=p.id)
        WHERE """ + condition + " ORDER BY r.task_id,r.round,r.id", params)
    output, first_valid, previous = [], {}, {}
    for row in rows:
        judged = _json(row["judge_json"], {})
        values = _json(row["values_json"], {})
        state = row["overall"] if row["overall"] in ("pass", "fail") else "pending"
        task_id = row["task_id"]
        is_first = state in ("pass", "fail") and task_id not in first_valid
        if is_first:
            first_valid[task_id] = row["id"]
        pdf = row.get("report_id") if row["plan_status"] == "closed" and row["latest_record_id"] == row["id"] else None
        standard = {"id": row["template_id"], "code": judged.get("template", {}).get("code") or "",
                    "title": row.get("template_title") or row.get("node_name") or "工序检验",
                    "version": row.get("template_revision") or "", "source": row.get("std_version") or ""}
        output.append({
            "key": "task:%d" % row["id"], "id": row["id"], "kind": "task",
            "title": row.get("node_name") or row.get("template_title") or KINDS["task"],
            "document_no": row["product_code"], "product_name": row["product_name"],
            "specification": row.get("product_model") or "", "manufacturer": "", "grade": "",
            "heat_no": str((values.get("heat_no") or {}).get("value") or ""),
            "inspection_date": row["created_at"][:10], "created_at": row["created_at"],
            "updated_at": row["created_at"], "signed_at": None,
            "owner": _owner(row), "dept": row.get("workgroup") or "", "state": state,
            "overall": state, "status": "submitted",
            "parent_id": previous.get(task_id), "root_id": row["id"], "round": row["round"],
            "task_id": task_id, "plan_id": row["plan_id"], "plan_code": row["plan_code"],
            "standard": standard, "version": row["round"], "snapshot": row["snapshot"],
            "issue_count": sum(1 for r in judged.get("rows", []) if r.get("status") in ("missing", "pending")),
            "source_url": "#/task/%d" % task_id,
            "preview_url": "/api/reports/%d/pdf" % pdf if pdf else None,
            "download_url": "/api/reports/%d/pdf" % pdf if pdf else None,
            "_data": {"values": values}, "_evaluation": judged, "_raw": row,
            "_first_inspection": is_first,
            "_retest": state in ("pass", "fail") and not is_first,
        })
        previous[task_id] = row["id"]
    roots = {}
    for item in output:
        roots.setdefault(item["task_id"], item["id"])
        item["root_id"] = roots[item["task_id"]]
    return output


def visible_records(c, actor, kind=None):
    """Reusable authorized projections for records, statistics and case tracking."""
    if not actor or actor["role"] not in ("admin", "supervisor"):
        return []
    if kind not in (None, "", "all", "steel", "task"):
        raise ValueError("未知检验类型")
    output = []
    if kind in (None, "", "all", "steel"):
        output.extend(_steel_records(c, actor))
    if kind in (None, "", "all", "task"):
        output.extend(_task_records(c, actor))
    return output


def _filters(args):
    kind = args.get("kind", "")
    state = args.get("state", "")
    if kind not in ("", "all", *KINDS):
        raise ValueError("未知检验类型")
    if state not in ("", "all", *STATE_FILTERS):
        raise ValueError("未知检验状态")
    query = args.get("q", "").strip()
    dept = args.get("dept", "").strip()
    defect = args.get("defect", "").strip()
    if len(query) > 160 or len(dept) > 80 or len(defect) > 160:
        raise ValueError("检索内容过长")
    owner = args.get("owner_id", "")
    try:
        owner = int(owner) if owner else None
        if owner is not None and owner <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("质量工程师编号无效") from None
    dates = []
    for key in ("date_from", "date_to"):
        value = args.get(key, "")
        if value:
            try:
                if date.fromisoformat(value).isoformat() != value:
                    raise ValueError
            except (TypeError, ValueError):
                raise ValueError("日期需为有效的年月日") from None
        dates.append(value)
    if all(dates) and dates[0] > dates[1]:
        raise ValueError("开始日期不能晚于结束日期")
    return dict(kind=kind, state=state, q=query.casefold(), dept=dept, owner_id=owner, defect=defect,
                date_from=dates[0], date_to=dates[1])


def filtered_records(c, actor, args):
    selected = _filters(args)
    output = []
    for record in visible_records(c, actor, selected["kind"]):
        state = selected["state"]
        state_matches = state in ("", "all", record["state"]) or (
            state == "final" and record["state"] in ("pass", "fail")) or (
            state == "unfinished" and record["state"] in ("draft", "pending")) or (
            state == "first" and record["_first_inspection"]) or (
            state == "retest" and record["_retest"])
        if not state_matches:
            continue
        if selected["defect"] and selected["defect"] not in failure_labels(record):
            continue
        if selected["owner_id"] is not None and record["owner"]["id"] != selected["owner_id"]:
            continue
        if selected["dept"] and record["dept"] != selected["dept"]:
            continue
        if selected["date_from"] and record["inspection_date"] < selected["date_from"]:
            continue
        if selected["date_to"] and record["inspection_date"] > selected["date_to"]:
            continue
        text = " ".join(str(record.get(k) or "") for k in
                        ("id", "key", "title", "document_no", "product_name", "specification", "manufacturer", "grade", "heat_no"))
        text += " " + record["owner"]["name"] + " " + record["dept"]
        if selected["q"] and selected["q"] not in text.casefold():
            continue
        output.append(record)
    return sorted(output, key=lambda r: (r["updated_at"], r["kind"], r["id"]), reverse=True)


def public_record(record):
    result = {key: value for key, value in record.items() if not key.startswith("_")}
    result["first_inspection"] = record["_first_inspection"]
    result["is_retest"] = record["_retest"]
    return result


def _paginate(items, args):
    try:
        page, size = int(args.get("page", 1)), int(args.get("page_size", 20))
    except (TypeError, ValueError):
        raise ValueError("页码或每页条数无效") from None
    if page < 1 or not 1 <= size <= 100:
        raise ValueError("页码或每页条数无效")
    pages = max(1, math.ceil(len(items) / size))
    page = min(page, pages)
    return {"items": [public_record(r) for r in items[(page - 1) * size:page * size]],
            "total": len(items), "page": page, "page_size": size, "pages": pages}


def _reading_status(value):
    return {"ok": "pass", "over": "fail", "na": "na"}.get(value, value or "missing")


def readings(record):
    output = []
    data = record["_data"]
    for row in record["_evaluation"].get("rows", []):
        key = row.get("key") or ""
        meta = data.get("values", {}).get(key) or {}
        requirement = row.get("spec") or row.get("note") or ""
        if record["kind"] == "steel":
            check = next((item for item in steel.CHECKS if item[0] == key), None)
            if check:
                requirement = check[2]
            elif key in ("thickness", "width"):
                nominal, lower, upper = (data.get(key + suffix) for suffix in ("_nominal", "_lower", "_upper"))
                if all(isinstance(v, (int, float)) for v in (nominal, lower, upper)):
                    requirement = "%g～%g mm" % (nominal + lower, nominal + upper)
        value = row.get("value", meta.get("value"))
        text = row.get("value_text")
        if text is None:
            text = "、".join(str(v) for v in value) if isinstance(value, list) else str(value if value is not None else "")
        output.append({"key": key, "label": row.get("label") or key, "unit": row.get("unit") or
                       ("mm" if record["kind"] == "steel" and key in ("thickness", "width") else ""),
                       "value": value, "value_text": text, "status": _reading_status(row.get("status")),
                       "requirement": requirement, "source": meta.get("source") or ("derived" if row.get("derived") else "manual"),
                       "confirmed": meta.get("confirmed", row.get("confirmed", meta.get("source") != "asr"))})
    for index, row in enumerate(data.get("extra_rows", [])):
        if not any(row.values()):
            continue
        output.append({"key": "extra_%d" % index, "label": row.get("name") or "补充尺寸",
                       "unit": "", "value": row.get("result") or "", "value_text": row.get("result") or "",
                       "status": {"合格": "pass", "不合格": "fail"}.get(row.get("verdict"), "pending"),
                       "requirement": row.get("requirement") or "", "source": "manual", "confirmed": True})
    return output


def _timeline(c, record):
    target = record["key"] if record["kind"] == "steel" else "task:%d" % record["task_id"]
    output = []
    for row in db.rows(c, "SELECT id,at,actor_name,action,detail FROM audit WHERE target=? ORDER BY id", (target,)):
        detail = _json(row.pop("detail"), {})
        if record["kind"] == "task" and row["action"] == "submit_record" and detail.get("round") != record["round"]:
            continue
        row["label"] = ACTION_LABELS.get(row["action"], row["action"])
        row["detail"] = detail
        summary = []
        if detail.get("round") is not None:
            summary.append("第 %s 轮检验" % detail["round"])
        if detail.get("overall") in ("pass", "fail", "pending"):
            summary.append({"pass": "合格", "fail": "不合格", "pending": "待核对"}[detail["overall"]])
        if detail.get("followup_record_id"):
            summary.append("关联后续表单 #%s" % detail["followup_record_id"])
        if row["action"] == "steel_deviation_decision":
            summary.append({"approved": "偏差申请已批准", "rejected": "偏差申请已驳回"}.get(detail.get("status"), "偏差审批已处理"))
        if isinstance(detail.get("note"), str) and detail["note"]:
            summary.append(detail["note"])
        row["summary"] = " · ".join(summary)
        output.append(row)
    return output


def detail(c, actor, kind, rid):
    all_records = visible_records(c, actor, kind)
    record = next((r for r in all_records if r["id"] == rid), None)
    if record is None:
        return None
    chain = sorted((r for r in all_records if r["root_id"] == record["root_id"]), key=lambda r: (r["created_at"], r["id"]))
    artifacts = []
    if record["kind"] == "steel":
        artifacts.append({"label": "本轮表单预览", "kind": "preview", "url": record["preview_url"]})
        if record["state"] in ("pass", "fail"):
            artifacts.append({"label": "定稿 Excel 原件", "kind": "xlsx", "url": record["download_url"],
                              "sha256": record.get("archive_sha256")})
    elif record["download_url"]:
        artifacts.append({"label": "所属计划归档报告", "kind": "pdf", "url": record["download_url"]})
    public = public_record(record)
    if kind == "steel":
        public["deviation"] = steel_standards.deviation(c, rid)
        public["approved_deviation"] = steel_standards.deviation(c, rid, "approved")
    return {"record": public, "readings": readings(record),
            "chain": [dict(public_record(r), current=r["id"] == rid) for r in chain],
            "timeline": _timeline(c, record), "artifacts": artifacts}


def failure_labels(record):
    if record["state"] != "fail":
        return set()
    return {r["label"] for r in readings(record) if r["status"] == "fail"} or {"批量判定不合格"}


def overview(records):
    states = Counter(r["state"] for r in records)
    completed = [r for r in records if r["state"] in ("pass", "fail")]
    first = [r for r in completed if r["_first_inspection"]]
    total = len(completed)
    metrics = {"final_count": total, "pass_count": states["pass"], "fail_count": states["fail"],
               "draft_count": states["draft"], "pending_count": states["pending"],
               "first_inspection_count": len(first), "retest_count": sum(r["_retest"] for r in completed),
               "pass_rate": round(100 * states["pass"] / total, 2) if total else None,
               "first_pass_rate": round(100 * sum(r["state"] == "pass" for r in first) / len(first), 2) if first else None}
    trend, people, defects = {}, {}, Counter()
    for record in completed:
        day = record["inspection_date"]
        row = trend.setdefault(day, {"date": day, "total": 0, "pass": 0, "fail": 0})
        row["total"] += 1
        row[record["state"]] += 1
        owner = record["owner"]
        person = people.setdefault((owner["id"], record["dept"]), {"owner_id": owner["id"], "name": owner["name"], "dept": record["dept"],
                                               "total": 0, "pass": 0, "fail": 0})
        person["total"] += 1
        person[record["state"]] += 1
        if record["state"] == "fail":
            defects.update(failure_labels(record))
    return {"metrics": metrics, "trend": [trend[k] for k in sorted(trend)],
            "defects": [{"label": label, "count": count} for label, count in defects.most_common()],
            "people": sorted(people.values(), key=lambda p: (-p["total"], p["name"])), "generated_at": db.now(),
            "definitions": {"date": "钢板按检验日期，工序按提交日期", "first_pass_rate": "首轮有效检验合格数 ÷ 首轮有效检验数",
                            "pass_rate": "合格表单数 ÷ 已有合格或不合格结论的表单数", "examples": "已排除示例数据"}}


def _csv_cell(value):
    text = str(value if value is not None else "")
    return "'" + text if text.lstrip().startswith(("=", "+", "-", "@")) else text


def export_csv(records):
    def generate():
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer)
        writer.writerow(["记录标识", "类型", "单据号/产品工号", "产品", "规格", "牌号", "炉批号", "生产单位",
                         "检验日期", "质量工程师", "班组", "状态", "标准版本", "原检标识", "创建时间", "更新时间"])
        yield "\ufeff" + buffer.getvalue()
        for record in records:
            buffer.seek(0)
            buffer.truncate(0)
            fields = [record["key"], record["title"], record["document_no"], record["product_name"], record["specification"],
                      record["grade"], record["heat_no"], record["manufacturer"], record["inspection_date"],
                      record["owner"]["name"], record["dept"],
                      {"draft": "草稿", "pending": "待核对", "pass": "合格", "fail": "不合格"}[record["state"]],
                      record["standard"]["version"], "%s:%d" % (record["kind"], record["root_id"]),
                      record["created_at"], record["updated_at"]]
            writer.writerow([_csv_cell(value) for value in fields])
            yield buffer.getvalue()
    response = Response(generate(), content_type="text/csv; charset=utf-8")
    response.headers["Content-Disposition"] = 'attachment; filename="inspection-ledger.csv"'
    return response


def register(app, conn, me, need_role):
    bp = Blueprint("management", __name__)

    @bp.before_request
    def consistent_read():
        # Several source tables and case counters must see the same instant
        # while employees are submitting records on other connections.
        connection = conn()
        if not connection.in_transaction:
            connection.execute("BEGIN")

    @bp.after_request
    def no_cache(response):
        conn().rollback()
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.errorhandler(ValueError)
    def invalid_filter(error):
        return jsonify(error=str(error)), 400

    @bp.route("/api/management/options")
    @need_role("admin", "supervisor")
    def options():
        actor = me()
        where, params = ("1", ()) if actor["role"] == "admin" else ("u.dept=?", (actor.get("dept") or "",))
        people = db.rows(conn(), "SELECT u.id,u.name,u.dept,u.role FROM users u WHERE " + where + " ORDER BY u.dept,u.name,u.id", params)
        depts = {p["dept"] for p in people if p["dept"]}
        depts.update(r["dept"] for r in visible_records(conn(), actor) if r["dept"])
        return jsonify(people=people, depts=sorted(depts),
                       kinds=[{"value": key, "label": label} for key, label in KINDS.items()])

    @bp.route("/api/management/records")
    @need_role("admin", "supervisor")
    def records():
        return jsonify(_paginate(filtered_records(conn(), me(), request.args), request.args))

    @bp.route("/api/management/records/<kind>/<int:rid>")
    @need_role("admin", "supervisor")
    def record(kind, rid):
        if kind not in KINDS:
            abort(404)
        result = detail(conn(), me(), kind, rid)
        if result is None:
            abort(404)
        return jsonify(result)

    @bp.route("/api/management/overview")
    @need_role("admin", "supervisor")
    def summary():
        import management_cases
        result = overview(filtered_records(conn(), me(), request.args))
        counts = management_cases.summary_counts(management_cases.scoped_defects(conn(), me(), request.args))
        result["metrics"].update(open_issues=counts["open"] + counts["following"],
                                  overdue_issues=counts["overdue"], closed_issues=counts["closed"])
        return jsonify(result)

    @bp.route("/api/management/export.csv")
    @need_role("admin", "supervisor")
    def csv_export():
        return export_csv(filtered_records(conn(), me(), request.args))

    @bp.route("/api/management/insights/report")
    @need_role("admin", "supervisor")
    def inspection_report():
        import management_insights
        import management_cases
        import management_ai
        actor = me()
        result = management_insights.report(visible_records(conn(), actor),
                 management_cases.scoped_defects(conn(), actor), actor, request.args)
        return jsonify(management_ai.attach(conn(), result, actor))

    @bp.route("/api/management/insights/dimensions")
    @need_role("admin", "supervisor")
    def dimension_insights():
        import management_insights
        actor = me()
        return jsonify(management_insights.dimensions(visible_records(conn(), actor, "steel"), actor, request.args))

    app.register_blueprint(bp)
