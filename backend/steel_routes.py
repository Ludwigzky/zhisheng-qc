# -*- coding: utf-8 -*-
"""Independent steel-stage routes; no replacement/reset of existing task data."""
import json
from flask import Blueprint, jsonify, make_response, request

import db
import access
import steel
import steel_archive
import steel_download
import steel_report
import steel_standards as standards
import steel_voice


def register(app, conn, me, need_login, process_audio):
    bp = Blueprint("steel", __name__)

    def decode(row):
        if row is None:
            return None
        result = dict(row)
        result["data"] = json.loads(result.pop("data_json"))
        saved_evaluation = json.loads(result.pop("evaluation_json"))
        result["evaluation"] = steel.evaluate(result["data"]) if result["status"] == "draft" else saved_evaluation
        result["standard"] = standards.by_id(conn(), result.get("standard_id"))
        result["deviation"] = standards.deviation(conn(), result["id"])
        actor = me()
        result["can_takeover"] = bool(actor and actor["role"]=="supervisor" and result["status"]=="draft"
            and access.can_steel(conn(), actor, result["id"]) and not db.row(conn(),
            "SELECT 1 FROM user_credentials WHERE user_id=? AND enabled=1",(result["owner_id"],)))
        child = followup(result["id"]) if result["status"] == "final" else None
        if child and not access.can_steel(conn(), me(), child["id"]):
            child = None
        result["followup_record_id"] = child["id"] if child else None
        result["followup_status"] = child["status"] if child else None
        return result

    def followup(rid):
        return db.row(conn(), "SELECT id, status FROM steel_records WHERE parent_id=? ORDER BY id LIMIT 1", (rid,))

    def get(rid):
        return decode(db.row(conn(), "SELECT * FROM steel_records WHERE id=?", (rid,)))

    def accessible(rid):
        return get(rid) if access.can_steel(conn(), me(), rid) else None

    def editable(record):
        return record and record["owner_id"] == me()["id"] and record["status"] == "draft"

    def audit(action, rid, detail):
        # db.audit commits immediately. These workflow events must instead share
        # the record transaction, including the automatic follow-up insertion.
        actor = me()
        db.insert(conn(), "audit", {"at": db.now(), "actor_id": actor["id"], "actor_name": actor["name"],
                                   "action": action, "target": "steel:%d" % rid,
                                   "detail": json.dumps(detail, ensure_ascii=False)})

    def insert_record(data, standard_id, parent_id=None, owner_id=None):
        now = db.now()
        template = steel_report.template_hash()
        rid = db.insert(conn(), "steel_records", {
            "standard_id": standard_id, "owner_id": me()["id"] if owner_id is None else owner_id, "created_at": now, "updated_at": now, "version": 1, "status": "draft",
            "data_json": json.dumps(data, ensure_ascii=False), "evaluation_json": json.dumps(steel.evaluate(data), ensure_ascii=False),
            "template_hash": template, "snapshot": steel.snapshot(data, template), "inspector_name": "", "signed_at": None, "parent_id": parent_id})
        audit("steel_create", rid, {"parent_id": parent_id})
        return rid

    def insert_followup(record, data, owner_id):
        fresh = steel.followup_data(data)
        std = record.get("standard")
        if std:
            standards.apply(fresh, std["criteria"])
        return insert_record(fresh, record["standard_id"], record["id"], owner_id)

    def update(rid, final=False):
        record = accessible(rid)
        if not record:
            return jsonify({"error": "钢板进货记录不存在"}), 404
        if not editable(record):
            return jsonify({"error": "只能编辑本人草稿；已确认记录请追加复检"}), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "请提交本次填写的表单及版本"}), 400
        if body.get("version") != record["version"]:
            return jsonify({"error": "记录已被其他页面更新，请重新打开"}), 409
        # Both save and finalize replace a whole form. Never let an incomplete
        # request replace previously saved controls with template defaults.
        submitted = body.get("data")
        required = record["data"].keys() - {"voice_pending"}  # derived review metadata on older clients
        if not isinstance(submitted, dict) or not required <= submitted.keys():
            return jsonify({"error": "未收到完整的本次表单，草稿未被替换，请重新提交"}), 400
        if "voice_pending" not in submitted:
            submitted = dict(submitted, voice_pending=record["data"].get("voice_pending", []))
        try:
            data = steel.normalize(submitted)
            standards.verify(data, standards.effective(conn(), record))
            evaluation = steel.evaluate(data)
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400
        if record["template_hash"] != steel_report.template_hash():
            return jsonify({"error": "原始模板已变化，请使用新版本记录"}), 409
        if final and not record["standard_id"]:
            return jsonify({"error":"历史草稿尚未绑定工艺标准，请先从已发布版本中选定标准"}), 409
        if final and record["deviation"] and record["deviation"]["status"] == "pending":
            return jsonify({"error":"本单偏差申请正在等待工艺审核"}), 409
        blocked = final and (bool(data.get("voice_pending")) or not evaluation["can_finalize"])
        sign = final and not blocked
        if sign:
            data["sampling_note"] = steel.SAMPLING_NOTE
            data["inspector_label"] = steel.INSPECTOR_LABEL
        inspector = me()["name"] if sign else ""
        # Even an unchanged rejected submission goes through the version check
        # in UPDATE, so another tab cannot replace its data between checks.
        unchanged = blocked and data == record["data"] and evaluation == record["evaluation"]
        now = record["updated_at"] if unchanged else db.now()
        snapshot = record["snapshot"] if unchanged else steel.snapshot({"data":data,"inspector":inspector},record["template_hash"])
        followup_id = None
        with conn():
            cursor = conn().execute("UPDATE steel_records SET data_json=?, evaluation_json=?, updated_at=?, version=version+?, status=?, snapshot=?, inspector_name=?, signed_at=? WHERE id=? AND version=? AND status='draft'", (
                json.dumps(data, ensure_ascii=False), json.dumps(evaluation, ensure_ascii=False), now, 0 if unchanged else 1,
                "final" if sign else "draft", snapshot, inspector, now if sign else None, rid, record["version"]))
            if cursor.rowcount != 1:
                conn().rollback()
                return jsonify({"error": "记录更新冲突，请重新打开"}), 409
            if sign and evaluation["overall"] == "fail":
                # The successful CAS owns the sole transition out of draft.
                # Insert the child before committing so a failure cannot leave
                # an immutable failed parent without its new inspection draft.
                child = followup(rid)
                followup_id = child["id"] if child else insert_followup(record, data, record["owner_id"])
                audit("steel_followup", rid, {"followup_record_id": followup_id})
            detail = {"snapshot": snapshot, "overall": evaluation["overall"], "followup_record_id": followup_id,
                      "standard_id":record["standard_id"], "deviation_id":(record["deviation"] or {}).get("id")}
            if blocked:
                detail["finalization_blocked_on"] = [issue["key"] for issue in evaluation["issues"]]
            audit("steel_finalize" if sign else "steel_save", rid, detail)
            saved = get(rid)
            if sign:
                steel_archive.archive_final(conn(), saved)
        if blocked:
            return jsonify({"error": "尚有缺项或待确认读数", "draft_saved": True,
                            "evaluation": evaluation, "record": saved}), 400
        return jsonify({"record": saved, "followup": get(followup_id) if followup_id else None,
                        "followup_record_id": followup_id})

    @bp.route("/api/steel/template")
    @need_login
    def template():
        return jsonify({"title": "进货检验记录", "record_code": steel.RECORD_CODE, "template_hash": steel_report.template_hash(),
                        "verify": [{"key": k, "label": label, "requirement": req} for k, label, req in steel.VERIFY],
                        "appearance": [{"key": k, "label": label, "requirement": req} for k, label, req in steel.APPEARANCE],
                        "blank": steel.blank_data(), "example": steel.employee_example(), "sampling_note": steel.SAMPLING_NOTE,
                        "legacy_sampling_note": steel.LEGACY_SAMPLING_NOTE,
                        "voice_targets": steel_voice.voice_targets(), "review_targets": steel_voice.review_targets()})

    @bp.route("/api/steel/voice-fields")
    @need_login
    def voice_fields():
        return jsonify({"voice_targets": steel_voice.voice_targets()})

    @bp.route("/api/steel/records", methods=["GET", "POST"])
    @need_login
    def records():
        if request.method == "GET":
            return steel_archive.list_response(conn(), me(), request.args)
        if me()["role"] not in ("inspector", "supervisor"):
            return jsonify({"error": "请使用质量工程师或室主任身份录入"}), 403
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error":"请提交已发布标准"}), 400
        try:
            with conn():
                conn().execute("BEGIN IMMEDIATE")
                choices = standards.active(conn())
                sid = body.get("standard_id")
                std = next((s for s in choices if type(sid) is int and s["id"]==sid), None)
                if sid is None and "data" in body:
                    # Older clients submitted a full form. Match the actual constraints
                    # instead of accepting user-provided limits as an approved standard.
                    data = steel.normalize(body["data"])
                    std = next((s for s in choices if s["criteria"]==standards.extract(data)), None)
                else:
                    data = standards.apply(steel.blank_data(), std["criteria"]) if std else None
                    if std and "data" in body:
                        data = steel.normalize(body["data"])
                if not std:
                    raise ValueError("请选择当前已发布的工艺标准")
                standards.verify(data, std["criteria"])
                if not data["is_example"] and not data["values"] and not data["document_no"]:
                    data["inspection_date"] = steel.blank_data()["inspection_date"]
                rid = insert_record(data, std["id"])
        except (TypeError, ValueError) as exc:
            return jsonify({"error":str(exc)}), 400
        return jsonify({"record": get(rid)}), 201

    @bp.route("/api/steel/evaluate", methods=["POST"])
    @need_login
    def evaluate():
        try:
            data = steel.normalize((request.get_json(silent=True) or {}).get("data", {}))
            return jsonify({"evaluation": steel.evaluate(data)})
        except (ValueError, TypeError) as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.route("/api/steel/records/<int:rid>", methods=["GET", "PUT"])
    @need_login
    def record(rid):
        if request.method == "PUT":
            return update(rid)
        r = accessible(rid)
        return (jsonify({"record": r}), 200) if r else (jsonify({"error": "记录不存在"}), 404)

    @bp.route("/api/steel/records/<int:rid>/finalize", methods=["POST"])
    @need_login
    def finalize(rid):
        try:
            return update(rid, final=True)
        except steel_archive.ArchiveError as exc:
            return jsonify(error="正式文件归档失败，已保留草稿："+str(exc)),409

    @bp.route("/api/steel/records/<int:rid>/takeover", methods=["POST"])
    @need_login
    def takeover(rid):
        body=request.get_json(silent=True)
        with conn():
            conn().execute("BEGIN IMMEDIATE")
            record=accessible(rid)
            if not record or not record["can_takeover"]:
                return jsonify(error="仅同组室主任可接手原负责人已停用的草稿"),403
            if not isinstance(body,dict) or body.get("version")!=record["version"]:
                return jsonify(error="草稿版本已变化，请重新打开"),409
            conn().execute("UPDATE steel_records SET owner_id=?,version=version+1,updated_at=? WHERE id=?",
                           (me()["id"],db.now(),rid))
            audit("steel_draft_takeover",rid,{"from":record["owner_id"],"to":me()["id"]})
        return jsonify(record=get(rid))

    @bp.route("/api/steel/records/<int:rid>/revise", methods=["POST"])
    @need_login
    def revise(rid):
        record = accessible(rid)
        if not record or record["status"] != "final":
            return jsonify({"error": "只能对已确认记录追加复检"}), 400
        if me()["role"] not in ("inspector", "supervisor"):
            return jsonify({"error": "当前身份不能发起复检"}), 403
        with conn():
            conn().execute("BEGIN IMMEDIATE")
            child = followup(rid)
            if child:
                child_id = child["id"]
            else:
                child_id = insert_followup(record, record["data"], me()["id"])
            audit("steel_followup_reuse" if child else "steel_followup", rid, {"followup_record_id": child_id})
        if not access.can_steel(conn(), me(), child_id):
            return jsonify({"error":"已有其他质量工程师负责这次复检"}), 403
        return jsonify({"record": get(child_id), "reused": bool(child)}), 200 if child else 201

    @bp.route("/api/steel/records/<int:rid>/preview")
    @need_login
    def preview(rid):
        record = accessible(rid)
        if not record:
            return jsonify({"error": "记录不存在"}), 404
        try:
            response = make_response(steel_archive.preview_html(conn(), record) if record["status"]=="final"
                                     else steel_report.html_preview(record))
        except steel_archive.ArchiveError as exc:
            return jsonify(error=str(exc)),409
        response.headers['Cache-Control'] = 'private, no-store, max-age=0'
        return response

    steel_download.register(bp, conn, me, need_login, get)
    standards.register(bp, conn, me, need_login, get, accessible)

    @bp.route("/api/steel/asr/parse", methods=["POST"])
    @need_login
    def parse():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify({"error": "语音请求必须是对象"}), 400
        current = body.get("current_key")
        if current is not None and (not isinstance(current, str) or current not in steel_voice.VOICE_TARGETS):
            return jsonify({"error": "未知钢板检验字段"}), 400
        try:
            return jsonify(steel_voice.parse_utterance(body.get("text"), current, body.get("asr_confidence")))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

    @bp.route("/api/steel/asr/audio", methods=["POST"])
    @need_login
    def audio():
        rid = request.form.get("record_id")
        if not rid or not rid.isdigit():
            return jsonify({"error": "请先建立钢板检验草稿"}), 400
        record = accessible(int(rid))
        if not editable(record):
            return jsonify({"error": "该记录不可录入"}), 403
        current = request.form.get("current_key") or None
        if current is not None and current not in steel_voice.VOICE_TARGETS:
            return jsonify({"error": "未知钢板检验字段"}), 400
        result = process_audio(request.form.get("denoise") in ("1", "true", "on"), steel_voice.schema())
        if result.get("error"):
            return jsonify(result), 400
        try:
            parsed = steel_voice.parse_utterance(result.get("text") or "", result.get("current_key") or current,
                                                  asr_confidence=result.get("confidence", 0))
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        result.update(assignments=parsed["assignments"], unknown=parsed["unknown"], normalized=parsed["normalized"])
        result["requires_confirmation"] = bool(result["assignments"] or result.get("requires_confirmation"))
        result["record_id"] = record["id"]
        db.audit(conn(), me(), "steel_asr", "steel:%d" % record["id"],
                 {"audio_id": result.get("audio_id"), "text": result.get("text"), "current_key": current})
        return jsonify(result)

    app.register_blueprint(bp)
