"""Backup/restore the QC data and complete verified archives for legacy signed records."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import steel_archive


def db_connection(path, mode):
    return sqlite3.connect(path.resolve(strict=True).as_uri() + "?mode=" + mode, uri=True)


def file_hash(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024*1024), b""):
            value.update(chunk)
    return value.hexdigest()


def inventory(root):
    files = {}
    for path in root.rglob("*"):
        if path.is_symlink() or root.resolve() not in path.resolve().parents:
            raise ValueError("备份目录含链接或目录外路径")
        if path.is_file() and path.relative_to(root).as_posix() != "manifest.json":
            files[path.relative_to(root).as_posix()] = file_hash(path)
    return files


def verify(folder):
    expected = json.loads((folder/"manifest.json").read_text(encoding="utf-8"))
    if expected.get("format") != 1 or expected.get("files") != inventory(folder):
        raise ValueError("文件清单或 SHA-256 校验失败")
    c = db_connection(folder/"data"/"demo.db", "ro")
    try:
        if c.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("备份数据库完整性检查失败")
        if c.execute("PRAGMA foreign_key_check").fetchone():
            raise ValueError("备份数据库包含失效关联")
        present = c.execute("SELECT 1 FROM sqlite_master WHERE name='steel_archives'").fetchone()
        if present:
            for key, content in c.execute("SELECT template_hash,content FROM steel_archive_templates"):
                if steel_archive.digest(content) != key:
                    raise ValueError("备份内的原始模板校验失败")
            for rid, content, checksum, preview, html_sum, matches in c.execute(
                """SELECT a.record_id,a.xlsx,a.xlsx_sha256,a.preview,a.preview_sha256,
                   a.snapshot=r.snapshot AND a.record_version=r.version AND a.template_hash=r.template_hash AND r.status='final'
                   FROM steel_archives a LEFT JOIN steel_records r ON r.id=a.record_id"""):
                if not matches or steel_archive.digest(content) != checksum or steel_archive.digest(preview.encode("utf-8")) != html_sum:
                    raise ValueError("备份中的归档记录 #%d 未通过校验" % rid)
    finally:
        c.close()


def publish_new_dir(destination, generate):
    destination = destination.resolve()
    if destination.exists():
        raise ValueError("请选择尚不存在的输出目录，以免覆盖现有文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".qc-staging-",dir=str(destination.parent)) as temporary:
        stage = Path(temporary)/"copy"
        stage.mkdir()
        generate(stage)
        verify(stage)
        stage.rename(destination)


def backup(root, destination):
    root = root.resolve(strict=True)
    source_dirs = (root/"data", root/"templates")
    if any(path.resolve() in (destination.resolve(), *destination.resolve().parents) for path in source_dirs):
        raise ValueError("备份不能放在正在备份的数据或模板目录内")

    def generate(stage):
        data = stage/"data"
        data.mkdir()
        source = db_connection(root/"data"/"demo.db", "ro")
        target = sqlite3.connect(str(data/"demo.db"))
        try:
            source.backup(target)
            # Ship one self-contained snapshot even when the live DB uses WAL.
            target.execute("PRAGMA journal_mode=DELETE")
        finally:
            source.close()
            target.close()
        for relative in ("data/audio", "data/reports", "templates"):
            folder = root / relative
            if folder.is_dir():
                if folder.is_symlink() or root not in folder.resolve().parents:
                    raise ValueError("附件或模板目录不能指向项目外")
                for item in folder.rglob("*"):
                    if item.is_symlink() or folder.resolve() not in item.resolve().parents:
                        raise ValueError("附件目录含链接，无法安全备份")
                    if item.is_file():
                        output = stage/relative/item.relative_to(folder)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(item, output)
        (stage/"manifest.json").write_text(json.dumps({"format":1,"files":inventory(stage)},ensure_ascii=False,indent=2),encoding="utf-8")
    publish_new_dir(destination, generate)


def restore(source, destination):
    verify(source)
    if source.resolve() in (destination.resolve(), *destination.resolve().parents):
        raise ValueError("恢复目录不能位于备份内")
    def generate(stage):
        for relative in inventory(source):
            target = stage/relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source/relative, target)
        shutil.copy2(source/"manifest.json", stage/"manifest.json")
    publish_new_dir(destination, generate)


def backfill(path, template=None):
    c = db_connection(path, "rw")
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys=ON")
    failures = []
    try:
        with c:
            steel_archive.ensure_schema(c)
            if template is not None:
                contents = template.read_bytes()
                key = steel_archive.digest(contents)
                if not c.execute("SELECT 1 FROM steel_records WHERE status='final' AND template_hash=?", (key,)).fetchone():
                    raise ValueError("提供的模板哈希与任何定稿记录均不匹配")
                steel_archive.remember_template(c, contents)
        records = c.execute("""SELECT id FROM steel_records r WHERE status='final' AND NOT EXISTS
            (SELECT 1 FROM steel_archives a WHERE a.record_id=r.id) ORDER BY id""").fetchall()
        for (rid,) in records:
            try:
                steel_archive.artifact(c, steel_archive.read_record(c,rid), "xlsx")
            except ValueError as error:
                failures.append((rid,str(error)))
        print("已补档 %d 份，未补档 %d 份" % (len(records)-len(failures),len(failures)))
        for rid, reason in failures:
            print("#%d    %s" % (rid,reason))
        return not failures
    finally:
        c.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    save = actions.add_parser("backup")
    save.add_argument("--root", type=Path, default=ROOT)
    save.add_argument("--out", type=Path, required=True)
    check = actions.add_parser("verify")
    check.add_argument("--backup", type=Path, required=True)
    recover = actions.add_parser("restore")
    recover.add_argument("--backup", type=Path, required=True)
    recover.add_argument("--out", type=Path, required=True)
    old = actions.add_parser("backfill")
    old.add_argument("--db", type=Path, required=True)
    old.add_argument("--template", type=Path)
    args = parser.parse_args()
    if args.action == "backup":
        backup(args.root, args.out)
    elif args.action == "restore":
        restore(args.backup,args.out)
    elif args.action == "verify":
        verify(args.backup)
    else:
        return 0 if backfill(args.db, args.template) else 1
    print("校验通过")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, sqlite3.Error, ValueError) as error:
        sys.exit(str(error))
