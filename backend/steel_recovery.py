# -*- coding: utf-8 -*-
"""Read-only reference for transcriptions logged for a steel record."""
import json

from flask import Blueprint, jsonify

import db
import access
import steel_voice


def register(app, conn, me, need_login):
    bp = Blueprint("steel_recovery", __name__)

    @bp.after_request
    def prevent_cache(response):
        response.headers["Cache-Control"] = "private, no-store"
        return response

    @bp.route("/api/steel/records/<int:rid>/voice-history")
    @need_login
    def voice_history(rid):
        record = db.row(conn(), "SELECT owner_id FROM steel_records WHERE id=?", (rid,))
        if not record:
            return jsonify({"error": "钢板进货记录不存在"}), 404
        user = me()
        if not access.can_steel(conn(),user,rid):
            return jsonify({"error": "只允许记录本人或室主任查看语音历史"}), 403

        rows = db.rows(conn(), """SELECT at, detail FROM audit
            WHERE target=? AND action='steel_asr' ORDER BY id DESC LIMIT 30""", ("steel:%d" % rid,))
        entries = []
        for row in rows:
            try:
                detail = json.loads(row["detail"])
            except (ValueError, TypeError):
                continue
            if not isinstance(detail, dict) or not isinstance(detail.get("text"), str) or not detail["text"].strip():
                continue
            key = detail.get("current_key")
            target = steel_voice.TARGETS.get(key) if isinstance(key, str) else None
            entries.append({"at": row["at"], "text": detail["text"], "field_label": target["label"] if target else None})
        return jsonify({"entries": entries})

    app.register_blueprint(bp)
