"""Published inspection limits. Each record pins a version; exceptions apply to one record."""
import json
import math
from flask import jsonify, request
import db
import steel

DIMENSIONS = ("thickness", "width")
LIMITS = tuple(d + "_" + p for d in DIMENSIONS for p in ("nominal", "lower", "upper"))
DEVIATIONS = tuple(k for k in LIMITS if not k.endswith("_nominal"))
FIELDS = ("specification",) + LIMITS
LOCKED_VOICE = set(FIELDS) | {"extra_%d_%s" % (i, prop) for i in range(2) for prop in ("name", "requirement")}


def init_schema(c):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS steel_standards(
        id INTEGER PRIMARY KEY, code TEXT NOT NULL, revision INTEGER NOT NULL,
        source TEXT NOT NULL, criteria TEXT NOT NULL, published_at TEXT NOT NULL,
        publisher_id INTEGER, UNIQUE(code,revision));
    CREATE TABLE IF NOT EXISTS steel_deviations(
        id INTEGER PRIMARY KEY, record_id INTEGER NOT NULL, standard_id INTEGER NOT NULL,
        applicant_id INTEGER NOT NULL, reason TEXT NOT NULL, limits_json TEXT NOT NULL,
        created_at TEXT NOT NULL, status TEXT NOT NULL, reviewer_id INTEGER,
        reviewed_at TEXT, decision TEXT);
    CREATE UNIQUE INDEX IF NOT EXISTS steel_one_pending_deviation
        ON steel_deviations(record_id) WHERE status='pending';
    """)
    columns = {row[1] for row in c.execute("PRAGMA table_info(steel_records)")}
    migrating = "standard_id" not in columns
    if migrating:
        c.execute("ALTER TABLE steel_records ADD COLUMN standard_id INTEGER REFERENCES steel_standards(id)")
    if not db.row(c, "SELECT 1 FROM steel_standards LIMIT 1"):
        criteria = extract(steel.blank_data())
        c.execute("INSERT INTO steel_standards(code,revision,source,criteria,published_at) VALUES(?,1,?,?,?)",
                  ("HW-JL-02A", "员工提供的钢卷检验原表（5.75×1800）", json.dumps(criteria), db.now()))
    # Only bind older drafts whose stored requirements match exactly. Never
    # rewrite a historical final, or approve old inspector-entered tolerances.
    if not migrating:
        return
    versions = active(c)
    for record in db.rows(c, "SELECT id,data_json FROM steel_records WHERE standard_id IS NULL AND status='draft'"):
        criteria = extract(json.loads(record["data_json"]))
        match = next((v for v in versions if criteria == v["criteria"]), None)
        if match:
            c.execute("UPDATE steel_records SET standard_id=? WHERE id=?", (match["id"], record["id"]))


def extract(data):
    result = {k: data.get(k) for k in FIELDS}
    rows = [{"name": row.get("name", ""), "requirement": row.get("requirement", "")}
            for row in data.get("extra_rows", [])]
    while rows and not any(rows[-1].values()):
        rows.pop()
    result["extra_rows"] = rows
    return result


def limits(data):
    fields = {k: steel.numeric(data.get(k)) for k in LIMITS}
    for d in DIMENSIONS:
        nominal, lower, upper = (fields[d + "_" + p] for p in ("nominal", "lower", "upper"))
        if nominal <= 0 or nominal + lower <= 0 or lower > upper or not math.isfinite(nominal + upper):
            raise ValueError("公称尺寸和允许的最小尺寸须大于零，下偏差不能大于上偏差")
    return fields


def text_field(value, label, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError("请填写有效的%s（最多%d字）" % (label, maximum))
    return value.strip()


def criteria_for(body):
    fields = limits(body)
    fields["specification"] = "*".join(steel.pretty(fields[d+"_nominal"]) for d in DIMENSIONS)
    extra = body.get("extra_rows", [])
    if not isinstance(extra, list) or len(extra) > 2:
        raise ValueError("只支持原表预留的两个补充尺寸行")
    fields["extra_rows"] = []
    for row in extra:
        if not isinstance(row, dict):
            raise ValueError("补充尺寸格式错误")
        fields["extra_rows"].append({k: text_field(row.get(k), label, 80) for k,label in
                                     (("name","补充尺寸名称"), ("requirement","补充尺寸标准"))})
    return fields


def decode(row):
    if row:
        row["criteria"] = json.loads(row["criteria"])
    return row


def by_id(c, sid):
    if type(sid) is not int:
        return None
    return decode(db.row(c, "SELECT * FROM steel_standards WHERE id=?", (sid,)))


def active(c):
    return [decode(row) for row in db.rows(c, """
        SELECT s.* FROM steel_standards s
        WHERE NOT EXISTS(SELECT 1 FROM steel_standards n WHERE n.code=s.code AND n.revision>s.revision)
        ORDER BY code""")]


def apply(data, criteria):
    for key in FIELDS:
        data[key] = criteria[key]
    data["extra_rows"] = [dict(row, result="", verdict="") for row in criteria["extra_rows"]]
    return data


def verify(data, criteria):
    if extract(data) != criteria:
        raise ValueError("规格、尺寸公差和补充尺寸要求须与本单绑定的工艺版本一致")
    for i, row in enumerate(data["extra_rows"]):
        if i >= len(criteria["extra_rows"]) and (row["result"] or row["verdict"]):
            raise ValueError("未发布的补充尺寸不能录入检测结果")


def deviation(c, rid, status=None):
    query = """SELECT d.*, a.name AS applicant, r.name AS reviewer FROM steel_deviations d
        LEFT JOIN users a ON a.id=d.applicant_id LEFT JOIN users r ON r.id=d.reviewer_id
        WHERE d.record_id=?"""
    args = [rid]
    if status:
        query += " AND d.status=?"
        args.append(status)
    row = db.row(c, query + " ORDER BY d.id DESC LIMIT 1", args)
    if row:
        row["limits"] = json.loads(row.pop("limits_json"))
    return row


def effective(c, record):
    std = by_id(c, record.get("standard_id"))
    if not std:
        return extract(record["data"])
    criteria = std["criteria"]
    approved = deviation(c, record["id"], "approved")
    if approved:
        criteria.update(approved["limits"])
    return criteria


def publish(c, actor, body):
    code = text_field(body.get("code"), "标准编号", 64)
    source = text_field(body.get("source"), "发布依据", 160)
    criteria = criteria_for(body)
    with c:
        c.execute("BEGIN IMMEDIATE")
        previous = c.execute("SELECT COALESCE(MAX(revision),0) FROM steel_standards WHERE code=?", (code,)).fetchone()[0]
        if previous != body.get("previous_revision", 0):
            raise ValueError("标准版本已更新，请刷新后以最新版发布")
        sid = db.insert(c, "steel_standards", {"code":code, "revision":previous+1, "source":source,
            "criteria":json.dumps(criteria, ensure_ascii=False), "published_at":db.now(),"publisher_id":actor["id"]})
        db.audit(c, actor, "steel_standard_publish", "standard:%d" % sid, {"code":code, "revision":previous+1, "source":source})
    return by_id(c, sid)


def _write_draft(c, record, data, sid):
    """Caller holds BEGIN IMMEDIATE and has checked ownership/version."""
    evaluation = steel.evaluate(data)
    snapshot = steel.snapshot({"data":data, "standard_id":sid}, record["template_hash"])
    c.execute("""UPDATE steel_records SET data_json=?,evaluation_json=?,standard_id=?,version=version+1,
                 snapshot=?,updated_at=? WHERE id=? AND status='draft'""",
              (json.dumps(data, ensure_ascii=False),json.dumps(evaluation, ensure_ascii=False),sid,
               snapshot,db.now(),record["id"]))


def register(bp, conn, me, need_login, get, accessible):
    @bp.route("/api/steel/standards", methods=["GET","POST"])
    @need_login
    def standards():
        if request.method == "GET":
            return jsonify(standards=active(conn()))
        if me()["role"] != "process":
            return jsonify(error="仅工艺人员可发布标准"), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify(error="请填写规格和标准版本依据"), 400
        try:
            return jsonify(standard=publish(conn(),me(),body)), 201
        except (TypeError, ValueError) as exc:
            return jsonify(error=str(exc)), 400

    @bp.route("/api/steel/records/<int:rid>/bind-standard", methods=["POST"])
    @need_login
    def bind(rid):
        body = request.get_json(silent=True) or {}
        if not isinstance(body,dict):
            return jsonify(error="请选择标准"),400
        c = conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            record = accessible(rid)
            if not record or record["status"]!="draft" or record["owner_id"]!=me()["id"]:
                return jsonify(error="仅可绑定本人的草稿"),403
            if record["standard_id"] or record["version"]!=body.get("version"):
                return jsonify(error="该单已经绑定标准或版本已变化"),409
            std = next((s for s in active(c) if s["id"]==body.get("standard_id")),None)
            if not std:
                return jsonify(error="请选择已发布的标准"),400
            # Keep prior measurements. If the supplementary dimensions differ
            # we require a matching published standard to avoid losing results.
            data = record["data"]
            old_extras = extract(data)["extra_rows"]
            if old_extras != std["criteria"]["extra_rows"] and any(
                    r.get("result") or r.get("verdict") for r in data.get("extra_rows",[])):
                return jsonify(error="已有补充尺寸读数，请选用同名同要求的标准或先保存更正后的补充尺寸"),409
            existing_rows = data["extra_rows"]
            apply(data,std["criteria"])
            for row,old in zip(data["extra_rows"],existing_rows):
                row.update(result=old.get("result",""),verdict=old.get("verdict",""))
            _write_draft(c,record,data,std["id"])
            db.audit(c,me(),"steel_standard_bind","steel:%d"%rid,{"standard_id":std["id"]})
        return jsonify(record=get(rid))

    @bp.route("/api/steel/records/<int:rid>/deviation", methods=["POST"])
    @need_login
    def apply_deviation(rid):
        body = request.get_json(silent=True)
        if not isinstance(body,dict):
            return jsonify(error="请填写单次偏差申请"),400
        c = conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            rec = accessible(rid)
            if not rec or rec["status"]!="draft" or rec["owner_id"]!=me()["id"]:
                return jsonify(error="仅可为本人草稿申请偏差"),403
            if rec["version"]!=body.get("version") or not rec["standard"]:
                return jsonify(error="请确认记录已绑定标准并刷新最新版本"),409
            if rec["deviation"] and rec["deviation"]["status"] in ("pending","approved"):
                return jsonify(error="本单已申请偏差"),409
            try:
                reason = text_field(body.get("reason"),"申请依据",300)
                changes = {k:steel.numeric(body.get(k)) for k in DEVIATIONS}
                current = rec["standard"]["criteria"]
                limits(dict(current,**changes))
                if all(current[k] == changes[k] for k in changes):
                    raise ValueError("申请偏差与已发布公差相同")
            except (TypeError,ValueError) as exc:
                return jsonify(error=str(exc)),400
            did=db.insert(c,"steel_deviations",{"record_id":rid,"standard_id":rec["standard_id"],
                "applicant_id":me()["id"],"created_at":db.now(),"status":"pending","reason":reason,
                "limits_json":json.dumps(changes)})
            c.execute("UPDATE steel_records SET version=version+1,updated_at=? WHERE id=?",(db.now(),rid))
            db.audit(c,me(),"steel_deviation_request","steel:%d"%rid,{"deviation_id":did})
        return jsonify(record=get(rid)),201

    @bp.route("/api/steel/deviations", methods=["GET"])
    @need_login
    def pending_deviations():
        actor=me()
        if actor["role"]!="process":
            return jsonify(error="仅工艺人员可审核偏差"),403
        rows=db.rows(conn(),"""SELECT d.id,d.record_id,d.reason,d.limits_json,d.standard_id,
            d.created_at,u.name applicant FROM steel_deviations d JOIN users u ON u.id=d.applicant_id
            WHERE d.status='pending' AND u.dept=? ORDER BY d.id""",(actor["dept"],))
        for r in rows:
            r["limits"]=json.loads(r.pop("limits_json"))
            r["standard"]=by_id(conn(),r["standard_id"])
        return jsonify(requests=rows)

    @bp.route("/api/steel/deviations/<int:did>/decision", methods=["POST"])
    @need_login
    def decide(did):
        actor=me()
        if actor["role"]!="process":
            return jsonify(error="仅工艺人员可审核偏差"),403
        body=request.get_json(silent=True)
        if not isinstance(body,dict) or body.get("decision") not in ("approved","rejected"):
            return jsonify(error="请选择批准或驳回"),400
        try:
            note=text_field(body.get("note"),"审核依据",300)
        except ValueError as exc:
            return jsonify(error=str(exc)),400
        c=conn()
        with c:
            c.execute("BEGIN IMMEDIATE")
            item=db.row(c,"""SELECT d.*,u.dept FROM steel_deviations d JOIN users u ON u.id=d.applicant_id
                        WHERE d.id=? AND u.dept=?""",(did,actor["dept"]))
            if not item or item["applicant_id"]==actor["id"]:
                return jsonify(error="无此申请的审核权限"),403
            rec=get(item["record_id"])
            if item["status"]!="pending" or rec["status"]!="draft":
                return jsonify(error="此申请已处理"),409
            c.execute("UPDATE steel_deviations SET status=?,reviewer_id=?,reviewed_at=?,decision=? WHERE id=?",
                      (body["decision"],actor["id"],db.now(),note,did))
            data=rec["data"]
            if body["decision"]=="approved":
                data.update(json.loads(item["limits_json"]))
            _write_draft(c,rec,data,rec["standard_id"])
            db.audit(c,actor,"steel_deviation_decision","steel:%d"%rec["id"],{"deviation_id":did,"status":body["decision"],"note":note})
        return jsonify(ok=True)
