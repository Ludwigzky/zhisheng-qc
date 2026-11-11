"""Object-level access for inspection records, production plans and audit history."""
import re
import db


def steel_scope(actor, alias="r"):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", alias):
        raise ValueError("invalid table alias")
    if not actor:
        return "0", ()
    owner = alias + ".owner_id"
    if actor["role"] == "admin":
        return "1", ()
    if actor["role"] == "supervisor" and actor.get("dept"):
        return owner + " IN (SELECT id FROM users WHERE dept=?)", (actor["dept"],)
    if actor["role"] == "inspector":
        return owner + "=?", (actor["id"],)
    return "0", ()


def can_steel(c, actor, rid):
    where, args = steel_scope(actor)
    return bool(db.row(c, "SELECT 1 FROM steel_records r WHERE r.id=? AND "+where, (rid,) + args))


def can_plan(c, actor, pid):
    if not actor:
        return False
    plan = db.row(c, "SELECT workgroup FROM plans WHERE id=?", (pid,))
    return bool(plan and (actor["role"] == "admin" or
                (actor["role"] in ("inspector", "supervisor") and actor["dept"] and plan["workgroup"] == actor["dept"])))


def audit_rows(c, actor, target=None, limit=100):
    where, args = [], []
    if target:
        where.append("target=?")
        args.append(target)
    if actor["role"] == "supervisor":
        where.append("actor_id IN (SELECT id FROM users WHERE dept=? AND role <> 'admin')")
        args.append(actor["dept"])
    elif actor["role"] != "admin":
        where.append("actor_id=?")
        args.append(actor["id"])
    sql = "SELECT * FROM audit" + (" WHERE " + " AND ".join(where) if where else "")
    return db.rows(c, sql + " ORDER BY id DESC LIMIT ?", tuple(args + [limit]))
