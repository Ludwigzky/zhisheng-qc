# -*- coding: utf-8 -*-
"""Authenticated workbook exports and short-lived links for phone download managers."""
import hashlib
import logging
import os
import re
import secrets
import unicodedata
from urllib.parse import quote

from flask import jsonify, make_response, request, url_for
from itsdangerous import BadData, URLSafeTimedSerializer
from werkzeug.exceptions import RequestedRangeNotSatisfiable

import db
import steel_archive


TICKET_SECONDS = 300
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class RedactDownloadTicket(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if "ticket=" in message:
            record.msg = re.sub(r"([?&]ticket=)[A-Za-z0-9._-]+", r"\1[redacted]", message)
            record.args = ()
        return True


def workbook_filename(record):
    # Leave space for the Chinese prefix and extension within Android's
    # component limit (usually 255 UTF-8 bytes).
    number = "".join("_" if unicodedata.category(c).startswith("C") or c in '<>:"/\\|?*' else c
                     for c in record["data"]["document_no"])
    number = number.encode("utf-8")[:180].decode("utf-8", errors="ignore").strip(". ") or str(record["id"])
    suffix = "_示例" if record["data"].get("is_example") else ""
    return "进货检验记录_%s%s.xlsx" % (number, suffix)


def workbook_response(record, payload):
    response = make_response(payload)
    response.headers["Content-Type"] = MIME
    response.headers["Content-Disposition"] = (
        'attachment; filename="steel-incoming-%d.xlsx"; filename*=UTF-8\'\'%s'
        % (record["id"], quote(workbook_filename(record), safe="")))
    response.headers["Accept-Ranges"] = "bytes"
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.set_etag(hashlib.sha256(payload).hexdigest())
    return response.make_conditional(request, accept_ranges=True, complete_length=len(payload))


def register(bp, conn, me, need_login, get_record):
    @bp.errorhandler(steel_archive.ArchiveError)
    def archive_error(error):
        return jsonify(error=str(error)), 409

    # The launch script sets a random QC_SECRET shared by all its workers.
    # Direct Python invocations must not sign grants using the demo's public default.
    signer = URLSafeTimedSerializer(os.environ.get("QC_SECRET") or secrets.token_bytes(32),
                                    salt="steel-xlsx-download-v1")
    logging.getLogger("werkzeug").addFilter(RedactDownloadTicket())

    def serve(record, actor):
        try:
            response = workbook_response(record, steel_archive.artifact(conn(), record, "xlsx"))
        except RequestedRangeNotSatisfiable as exc:
            return exc.get_response(request.environ)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 409
        if request.method == "GET" and response.status_code == 200:
            db.audit(conn(), actor, "steel_export", "steel:%d" % record["id"], {"snapshot": record["snapshot"]})
        return response

    @bp.route("/api/steel/records/<int:rid>/xlsx")
    @need_login
    def xlsx(rid):
        import access
        if not access.can_steel(conn(), me(), rid):
            return jsonify({"error": "无权查看此记录或记录不存在"}), 404
        record = get_record(rid)
        if not record or record["status"] != "final":
            return jsonify({"error": "请先核对并确认记录，再下载正式Excel"}), 409
        return serve(record, me())

    @bp.route("/api/steel/records/<int:rid>/download-link", methods=["POST"])
    @need_login
    def download_link(rid):
        import access
        if not access.can_steel(conn(), me(), rid):
            return jsonify({"error": "无权查看此记录或记录不存在"}), 404
        record = get_record(rid)
        if not record or record["status"] != "final":
            return jsonify({"error": "请先核对并确认记录，再下载正式Excel"}), 409
        actor = me()
        ticket = signer.dumps([rid, record["version"], record["snapshot"], actor["id"]])
        url = url_for(".signed_xlsx", rid=rid, filename="steel-%d.xlsx" % rid, ticket=ticket)
        response = jsonify(url=url, filename=workbook_filename(record), expires_in=TICKET_SECONDS)
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        db.audit(conn(), actor, "steel_download_link", "steel:%d" % rid, {"snapshot": record["snapshot"]})
        return response

    @bp.route("/api/steel/records/<int:rid>/download/<filename>")
    def signed_xlsx(rid, filename):
        if filename != "steel-%d.xlsx" % rid:
            return jsonify({"error": "文件不存在"}), 404
        try:
            allowed_id, version, snapshot, actor_id = signer.loads(
                request.args.get("ticket", ""), max_age=TICKET_SECONDS)
        except BadData:
            return jsonify({"error": "下载链接已失效，请返回检验记录重新下载"}), 403
        if allowed_id != rid:
            return jsonify({"error": "下载链接与检验记录不匹配"}), 403
        record = get_record(rid)
        if not record or record["status"] != "final" or (record["version"], record["snapshot"]) != (version, snapshot):
            return jsonify({"error": "下载链接与当前定稿不匹配，请重新下载"}), 403
        import access
        import identity
        actor = identity.active_user(conn(), actor_id)
        if not actor or not access.can_steel(conn(), actor, rid):
            return jsonify({"error": "下载链接已失效，请重新登录"}), 403
        return serve(record, actor)
