"""Store each signed workbook in the same SQLite transaction as its inspection."""
import hashlib
import io
import json
import math
import zipfile
from datetime import date

from flask import jsonify

import db
import steel
import steel_report


class ArchiveError(ValueError):
    """A formal document could not be safely generated or verified."""


def digest(data):
    return hashlib.sha256(data).hexdigest()


def remember_template(c, contents):
    key = digest(contents)
    c.execute("INSERT OR IGNORE INTO steel_archive_templates(template_hash, content) VALUES (?, ?)", (key, contents))
    return key


def ensure_schema(c):
    c.execute("""CREATE TABLE IF NOT EXISTS steel_archive_templates (
        template_hash TEXT PRIMARY KEY, content BLOB NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS steel_archives (
        record_id INTEGER PRIMARY KEY REFERENCES steel_records(id),
        record_version INTEGER NOT NULL, snapshot TEXT NOT NULL,
        template_hash TEXT NOT NULL REFERENCES steel_archive_templates(template_hash),
        origin TEXT NOT NULL CHECK(origin IN ('signed','legacy')), archived_at TEXT NOT NULL,
        xlsx BLOB NOT NULL, xlsx_sha256 TEXT NOT NULL,
        preview TEXT NOT NULL, preview_sha256 TEXT NOT NULL)""")
    c.execute("CREATE INDEX IF NOT EXISTS steel_records_owner_id ON steel_records(owner_id, id)")
    c.execute("CREATE INDEX IF NOT EXISTS steel_records_parent ON steel_records(parent_id)")
    # A legacy final may only be read after a new template has been published.
    if steel_report.TEMPLATE.is_file():
        remember_template(c, steel_report.TEMPLATE.read_bytes())


def _template(c, expected):
    row = c.execute("SELECT content FROM steel_archive_templates WHERE template_hash=?", (expected,)).fetchone()
    if row is not None:
        if digest(row[0]) != expected:
            raise ArchiveError("归档模板校验失败，请从备份恢复")
        return row[0]
    if steel_report.TEMPLATE.is_file():
        current = steel_report.TEMPLATE.read_bytes()
        if digest(current) == expected:
            remember_template(c, current)
            return current
    raise ArchiveError("此旧记录缺少定稿时的原始模板，请部长提供匹配版本后补档")


def _verify_record(record):
    if record["status"] != "final" or not record.get("inspector_name") or not record.get("signed_at"):
        raise ArchiveError("仅已签认的正式记录可归档")
    expected = steel.snapshot({"data": record["data"], "inspector": record["inspector_name"]}, record["template_hash"])
    if record["snapshot"] != expected:
        raise ArchiveError("检验记录快照校验失败，请核对备份")


def archive_final(c, record, origin="signed"):
    """No commit: failure must also roll back the final record and its follow-up."""
    _verify_record(record)
    template = _template(c, record["template_hash"])
    try:
        xlsx = steel_report.build_xlsx(record, template)
        with zipfile.ZipFile(io.BytesIO(xlsx)) as package:
            if package.testzip() or "xl/worksheets/sheet1.xml" not in package.namelist():
                raise ValueError("生成的 Excel 文件不完整")
        preview = steel_report.html_preview(record, template)
    except (KeyError, ValueError, zipfile.BadZipFile) as error:
        raise ArchiveError("归档未完成，请核对员工模板和记录内容：%s" % error) from error
    c.execute("""INSERT INTO steel_archives
        (record_id, record_version, snapshot, template_hash, origin, archived_at, xlsx, xlsx_sha256, preview, preview_sha256)
        VALUES (?,?,?,?,?,?,?,?,?,?)""", (record["id"], record["version"], record["snapshot"],
        record["template_hash"], origin, db.now(), xlsx, digest(xlsx), preview, digest(preview.encode("utf-8"))))


def artifact(c, record, kind):
    """Retrieve verified original bytes. Only pre-upgrade finals need backfill."""
    if kind not in ("xlsx", "preview"):
        raise ValueError("未知归档内容")
    _verify_record(record)
    sql = "SELECT snapshot,template_hash,record_version,{0},{0}_sha256 FROM steel_archives WHERE record_id=?".format(kind)
    row = c.execute(sql, (record["id"],)).fetchone()
    if row is None:
        with c:
            c.execute("BEGIN IMMEDIATE")
            # A second download may already have archived it.
            row = c.execute(sql, (record["id"],)).fetchone()
            if row is None:
                archive_final(c, record, origin="legacy")
                row = c.execute(sql, (record["id"],)).fetchone()
    if tuple(row[:3]) != (record["snapshot"], record["template_hash"], record["version"]):
        raise ArchiveError("归档文件与已签认记录的版本不一致")
    payload, checksum = row[3:]
    if digest(payload.encode("utf-8") if kind == "preview" else payload) != checksum:
        raise ArchiveError("归档内容校验失败，请部长从备份恢复")
    return payload


def preview_html(c, record):
    return artifact(c, record, "preview")


def read_record(c, rid):
    row = db.row(c, "SELECT * FROM steel_records WHERE id=?", (rid,))
    if row is not None:
        row["data"] = json.loads(row.pop("data_json"))
        row["evaluation"] = json.loads(row.pop("evaluation_json"))
    return row


def list_response(c, actor, args):
    import access
    scope, bindings = access.steel_scope(actor, alias="r")
    filters, params = ["("+scope+")"], list(bindings)
    try:
        page = int(args.get("page", 1))
        per_page = int(args.get("page_size", 20))
        if page < 1 or not 1 <= per_page <= 100:
            raise ValueError("页码或每页条数无效")
        query = args.get("q", "").strip()
        if query:
            fields = ("document_no", "heat_no", "manufacturer", "grade")
            # instr treats user-entered % and _ as literal characters.
            terms = ["instr(lower(json_extract(r.data_json,'$."+field+"')), lower(?)) > 0" for field in fields]
            filters.append("(" + " OR ".join(terms) + ")")
            params.extend([query]*len(fields))
        status = args.get("status", "")
        if status == "draft":
            filters.append("r.status='draft'")
        elif status in ("pass","fail"):
            filters.append("r.status='final' AND json_extract(r.evaluation_json,'$.overall')=?")
            params.append(status)
        elif status:
            raise ValueError("未知检验状态")
        dates = []
        for name, operator in (("from", ">="), ("to", "<=")):
            value = args.get(name, "")
            if value:
                try:
                    if date.fromisoformat(value).isoformat() != value:
                        raise ValueError
                except ValueError:
                    raise ValueError("日期需为有效的年月日") from None
                dates.append(value)
                filters.append("json_extract(r.data_json,'$.inspection_date') "+operator+" ?")
                params.append(value)
        if len(dates) == 2 and dates[0] > dates[1]:
            raise ValueError("开始日期晚于结束日期")
    except (TypeError, ValueError) as exc:
        return jsonify(error=str(exc)), 400

    where = " AND ".join(filters)
    total = c.execute("SELECT COUNT(*) FROM steel_records r WHERE "+where, params).fetchone()[0]
    pages = max(1, math.ceil(total/per_page))
    page = min(page, pages)
    child_scope, child_params = access.steel_scope(actor, alias="candidate")
    rows = db.rows(c, """SELECT r.*, a.origin AS archive_origin, a.xlsx_sha256 AS archive_sha256,
        child.id AS followup_record_id, child.status AS followup_status
        FROM steel_records r LEFT JOIN steel_archives a ON a.record_id=r.id
        LEFT JOIN steel_records child ON child.id=(
            SELECT min(candidate.id) FROM steel_records candidate WHERE candidate.parent_id=r.id AND ("""
        +child_scope+")) WHERE "+where+" ORDER BY r.id DESC LIMIT ? OFFSET ?",
        list(child_params)+params+[per_page,(page-1)*per_page])
    for r in rows:
        r["data"] = json.loads(r.pop("data_json"))
        r["evaluation"] = json.loads(r.pop("evaluation_json"))
        # Preserve the prior API's failed-parent link, including its status.
        if r["status"] != "final" or r["evaluation"]["overall"] != "fail":
            r["followup_record_id"] = None
            r["followup_status"] = None
    return jsonify(records=rows, total=total, page=page, page_size=per_page, pages=pages)
