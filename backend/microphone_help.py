# -*- coding: utf-8 -*-
"""Local microphone help, public certificate download and bounded diagnostics.

Only the existing public certificate is read. This module never reads a key,
changes device trust settings, or sends diagnostics outside this application.
"""
import hashlib
from pathlib import Path
import re
import ssl
from urllib.parse import urlsplit

from flask import Blueprint, jsonify, make_response, request

import db


PERMISSIONS = {"granted", "denied", "prompt", "unknown", "unsupported"}
CERTIFICATE_NAME = "qc-demo-ca.cer"


def _text(value, maximum):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x1f\x7f]", " ", value)[:maximum]


def _origin(value):
    """Compare browser origins without accepting arbitrary paths or user info."""
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ("http", "https") or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            return None
        return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == "https" else 80)
    except (TypeError, ValueError):
        return None


def register(app, root, conn, me, need_login):
    bp = Blueprint("microphone_help", __name__)
    certificate_path = Path(root) / "certs" / "cert.pem"

    def certificate_der():
        # Fixed path, no filename supplied by a request and no key access.
        try:
            return ssl.PEM_cert_to_DER_cert(certificate_path.read_text(encoding="ascii"))
        except (OSError, ValueError, UnicodeError):
            return None

    @bp.after_request
    def response_headers(response):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @bp.route("/api/asr/certificate")
    def certificate():
        payload = certificate_der()
        if not payload:
            return jsonify({"error": "本机 HTTPS 公共证书暂不可下载，请检查演示服务证书配置"}), 404
        response = make_response(payload)
        response.headers["Content-Type"] = "application/pkix-cert"
        response.headers["Content-Disposition"] = 'attachment; filename="%s"' % CERTIFICATE_NAME
        return response

    @bp.route("/api/asr/microphone-help")
    def microphone_help():
        payload = certificate_der()
        return jsonify({
            "certificate": {
                "available": bool(payload),
                "download_url": "/api/asr/certificate" if payload else None,
                "filename": CERTIFICATE_NAME,
                "sha256": hashlib.sha256(payload).hexdigest() if payload else None,
            },
            "steps": [
                {"id": "browser", "title": "用独立浏览器打开", "text": "复制当前完整 HTTPS 地址到独立 Chrome 或 Edge，保持页面在前台，再点击麦克风。"},
                {"id": "permission", "title": "允许网站和应用使用麦克风", "text": "在浏览器的网站设置中允许此网站使用麦克风，同时在手机设置的应用权限中允许此浏览器使用麦克风。"},
                {"id": "certificate", "title": "需要时手动信任演示证书", "text": "若浏览器仍提示证书不受信任，可下载本机公共证书，在手机系统设置中搜索“安装证书”，选择“CA 证书”并手动确认。安装会增加系统信任；请确认文件来自你运行演示的电脑，演示结束后可移除。不同手机菜单名称可能不同。"},
            ],
        })

    @bp.route("/api/asr/microphone-report", methods=["POST"])
    @need_login
    def microphone_report():
        # Browser JSON posts normally contain Origin. Reject cross-origin
        # browser submissions; origin supplied in JSON is always ignored.
        incoming_origin = request.headers.get("Origin")
        if incoming_origin is not None and _origin(incoming_origin) != _origin(request.host_url):
            return jsonify({"error": "仅接受当前演示网站的录音诊断"}), 403
        if request.content_length is not None and request.content_length > 8192:
            return jsonify({"error": "录音诊断内容过长"}), 413
        if len(request.get_data(cache=True)) > 8192:
            return jsonify({"error": "录音诊断内容过长"}), 413
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            return jsonify({"error": "录音诊断必须为 JSON 对象"}), 400
        permission = body.get("permission")
        detail = {
            "secure": body.get("secure") if type(body.get("secure")) is bool else None,
            "permission": permission if isinstance(permission, str) and permission in PERMISSIONS else "unknown",
            "audio": _text(body.get("audio"), 32),
            "error": _text(body.get("error"), 80),
            "phase": _text(body.get("phase"), 32),
            "ui_version": _text(body.get("ui_version"), 40),
            "user_agent": _text(request.user_agent.string, 400),
            "origin": _text(request.host_url, 256),
        }
        db.audit(conn(), me(), "mic_diagnostic", "microphone", detail)
        return jsonify({"ok": True})

    app.register_blueprint(bp)
