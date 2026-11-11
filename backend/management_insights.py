"""Local, evidence-linked interpretations. No model calls or changes to verdicts."""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
import math

import management


RULE_VERSION = "inspection-insights-1"
WINDOW = 5
MIN_DAYS = 3
EDGE = 0.10


def today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def parse_date(value, label):
    try:
        parsed = date.fromisoformat(value)
        if parsed.isoformat() != value:
            raise ValueError
        return parsed
    except (ValueError, TypeError):
        raise ValueError(label + "需为有效的年月日") from None


def department(args, actor):
    dept = args.get("dept", "").strip()
    if len(dept) > 80:
        raise ValueError("班组名称过长")
    # Filtering never enlarges the scope already enforced by visible_records.
    return dept or (actor.get("dept", "") if actor["role"] != "admin" else "")


def reference(record):
    return {"key": record["key"], "kind": record["kind"], "id": record["id"],
            "document_no": record["document_no"], "title": record["title"],
            "date": record["inspection_date"], "dept": record["dept"],
            "owner": record["owner"]["name"], "state": record["state"],
            "url": "#/management-record/" + record["key"]}


def metrics(records):
    return management.overview(records)["metrics"]


def report(records, cases, actor, args, current_day=None):
    current_day = current_day or today()
    period = args.get("period", "day")
    if period not in ("day", "week"):
        raise ValueError("报告类型仅支持日报或周报")
    anchor = parse_date(args.get("date") or current_day.isoformat(), "报告日期")
    if anchor > current_day:
        raise ValueError("报告日期不能晚于今天")
    start = anchor if period == "day" else anchor - timedelta(days=anchor.weekday())
    end = anchor if period == "day" else min(start + timedelta(days=6), current_day)
    step = timedelta(days=1 if period == "day" else 7)
    try:
        previous_start, previous_end = start - step, end - step
    except OverflowError:
        raise ValueError("报告日期过早") from None
    dept = department(args, actor)
    scoped = [r for r in records if not dept or r["dept"] == dept]
    in_period = lambda r, lo, hi: lo.isoformat() <= r["inspection_date"] <= hi.isoformat()
    selected = [r for r in scoped if in_period(r, start, end)]
    previous = [r for r in scoped if in_period(r, previous_start, previous_end)]
    current_metrics, previous_metrics = metrics(selected), metrics(previous)
    # This is a live read, not a reconstruction of historical case states.
    backlog = [r for r in scoped if r["inspection_date"] <= end.isoformat() and r["state"] in ("draft", "pending")
               and (r["kind"] != "task" or r["_raw"].get("latest_record_id") == r["id"])]
    open_cases = [r for r in cases if (not dept or r["dept"] == dept)
                  and r["inspection_date"] <= end.isoformat() and r["case"]["status"] != "closed"]
    todos = []
    for item in open_cases:
        case = item["case"]
        todos.append({"record": reference(item), "type": "case", "overdue": case["overdue"],
                      "assignee": case["assignee_name"] or "未分派", "due_date": case["due_date"],
                      "action": "核对合格复检并完成跟进" if item["has_passed_followup"] else "跟进不合格项并核对处置结果"})
    for item in backlog:
        todos.append({"record": reference(item), "type": item["state"], "overdue": False,
                      "assignee": item["owner"]["name"], "due_date": None,
                      "action": "完成填写并核对定稿" if item["state"] == "draft" else "核对未确认的检验结果"})
    todos.sort(key=lambda t: (not t["overdue"], t["type"] != "case", t["due_date"] or "9999-12-31", t["record"]["date"], t["record"]["key"]))
    groups = defaultdict(list)
    for item in selected:
        groups[item["dept"]].append(item)
    for item in todos:
        groups.setdefault(item["record"]["dept"], [])
    teams = [{"dept": name, "metrics": metrics(items),
              "todo_count": sum(t["record"]["dept"] == name for t in todos),
              "records": [reference(r) for r in sorted(items, key=lambda r: (r["inspection_date"], r["key"]))]}
             for name, items in sorted(groups.items())]
    m = current_metrics
    sentences = ["本期已确认 %d 份检验表单，其中合格 %d 份、不合格 %d 份；复检 %d 份。" %
                 (m["final_count"], m["pass_count"], m["fail_count"], m["retest_count"])]
    if m["first_inspection_count"]:
        sentences.append("首检合格率为 %g%%，分母为 %d 份首轮已确认表单；复检结果不覆盖原首检。" %
                         (m["first_pass_rate"], m["first_inspection_count"]))
    else:
        sentences.append("本期没有已确认首检，暂不评价首检合格率。")
    delta = None
    if m["first_pass_rate"] is not None and previous_metrics["first_pass_rate"] is not None:
        delta = round(m["first_pass_rate"] - previous_metrics["first_pass_rate"], 2)
        sentences.append("较上一可比时段，首检合格率%s %g 个百分点；两期样本量分别为 %d 和 %d 份，仅作描述性比较。" %
                         ("上升" if delta >= 0 else "下降", abs(delta), m["first_inspection_count"], previous_metrics["first_inspection_count"]))
    else:
        sentences.append("本期或上一可比时段缺少已确认首检，暂不作合格率变化比较。")
    counts = Counter(label for r in selected for label in management.failure_labels(r))
    if counts:
        label, count = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[0]
        sentences.append("本期出现最多的不合格项目为“%s”，涉及 %d 份表单，可展开原单核查。" % (label, count))
    overdue = sum(t["overdue"] for t in todos)
    sentences.append("当前仍有 %d 项异常跟进未关闭（%d 项逾期），另有 %d 份未完成表单；待办包含本期之前的积压。" %
                     (len(open_cases), overdue, len(backlog)))
    return {"mode": "local_rules", "rule_version": RULE_VERSION,
            "generated_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
            "period": period, "anchor": anchor.isoformat(), "date_from": start.isoformat(), "date_to": end.isoformat(),
            "previous_from": previous_start.isoformat(), "previous_to": previous_end.isoformat(),
            "partial_week": period == "week" and end < start + timedelta(days=6),
            "dept": dept, "metrics": m, "previous_metrics": previous_metrics, "first_pass_change_pp": delta,
            "sentences": sentences, "teams": teams, "todos": todos,
            "defects": [{"label": label, "form_count": count} for label, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))],
            "notes": ["按检验日期统计，排除示例；草稿及待核对表单不计入合格率。数量单位为表单，不是钢卷或抽检件数。",
                      "统计指标由系统计算；AI 解读按需调用，建议须核对，不改变正式检验结论。",
                      "周报按周一至周日，本周截至今天；与上周同长度时段比较。",
                      "历史日期报告按当前保存数据重算，不是当时的归档快照。待办显示所选期末及以前记录的当前状态，逾期按今天判断。"]}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def dimension_status(points, low, high):
    recent = points[-WINDOW:]
    days = len({p["record"]["date"] for p in recent})
    enough = len(recent) == WINDOW and days >= MIN_DAYS
    last = recent[-1]
    span = high - low
    eps = span * 1e-9
    result = {"enough_data": enough, "window_count": len(recent), "day_count": days,
              "status": "insufficient", "label": "数据不足", "direction": None,
              "message": "需最近至少 5 份有效首检表单，覆盖至少 3 个检验日期，才能判断持续变化。"}
    if last["min"] < low - eps or last["max"] > high + eps:
        result.update(status="out_of_range", label="最新读数超差", message="最新首检表单存在超出本单公差的读数，请核对原单及异常处置。" )
        return result
    if not enough:
        return result
    means = [p["mean"] for p in recent]
    upper = all(b >= a - eps for a, b in zip(means, means[1:])) and means[-1] - means[0] >= EDGE * span - eps and means[-1] >= high - EDGE * span - eps
    lower = all(b <= a + eps for a, b in zip(means, means[1:])) and means[0] - means[-1] >= EDGE * span - eps and means[-1] <= low + EDGE * span + eps
    # An already out-of-tolerance mean is not described as approaching a limit.
    if all(low - eps <= p["min"] <= p["max"] <= high + eps for p in recent) and (upper or lower):
        direction = "上限" if upper else "下限"
        result.update(status="approaching", label="持续接近" + direction, direction=direction,
                      message="最近 5 份首检的表单均值持续向%s移动，累计变化达到公差带宽的 10%%，最新均值进入边界 10%% 区域。建议关注后续实测并核对量具状态。" % direction)
    elif last["min"] <= low + EDGE * span + eps or last["max"] >= high - EDGE * span - eps:
        result.update(status="near_limit", label="最新读数靠近边界", message="最新表单有读数进入公差边界 10% 区域，尚未满足持续接近条件。")
    else:
        result.update(status="no_signal", label="未触发提醒", message="当前窗口未触发接近公差边界的观察规则；不代表整卷质量保证或未来预测。")
    return result


def dimensions(records, actor, args, current_day=None):
    current_day = current_day or today()
    end = parse_date(args.get("date_to") or current_day.isoformat(), "结束日期")
    try:
        start = parse_date(args.get("date_from") or (end - timedelta(days=89)).isoformat(), "开始日期")
    except OverflowError:
        raise ValueError("日期范围过早") from None
    if start > end or end > current_day:
        raise ValueError("日期范围需从早到晚，且不能晚于今天")
    dept = department(args, actor)
    groups, skipped = {}, Counter()
    for record in records:
        if record["kind"] != "steel" or (dept and record["dept"] != dept) or not start.isoformat() <= record["inspection_date"] <= end.isoformat():
            continue
        if record["state"] not in ("pass", "fail"):
            skipped["未确认表单"] += 1
            continue
        if not record["_first_inspection"]:
            skipped["复检表单"] += 1
            continue
        data, standard = record["_data"], record["standard"]
        if not standard["id"] or not record["specification"] or not record["manufacturer"] or not record["grade"]:
            skipped["缺少规格、生产单位、牌号或标准版本"] += 1
            continue
        for field, label in (("thickness", "厚度"), ("width", "宽度")):
            limits = [data.get(field + suffix) for suffix in ("_nominal", "_lower", "_upper")]
            entry = data.get("values", {}).get(field) or {}
            values = entry.get("value")
            if not all(finite(v) for v in limits) or limits[0] <= 0 or limits[1] >= limits[2]:
                skipped["无效公差项目"] += 1
                continue
            if not isinstance(values, list) or not values or not all(finite(v) and v > 0 for v in values) or (entry.get("source") == "asr" and not entry.get("confirmed")):
                skipped["无效或未确认读数项目"] += 1
                continue
            nominal, minus, plus = limits
            low, high = nominal + minus, nominal + plus
            if not finite(low) or not finite(high) or not finite(high - low) or high <= low:
                skipped["无效公差项目"] += 1
                continue
            group_key = (record["dept"], record["manufacturer"], record["grade"], record["specification"],
                         standard["id"], standard["version"], field, *limits)
            if group_key not in groups:
                groups[group_key] = {"dept": record["dept"], "manufacturer": record["manufacturer"], "grade": record["grade"],
                                     "specification": record["specification"], "standard_id": standard["id"],
                                     "standard_code": standard["code"], "standard_version": standard["version"],
                                     "field": field, "dimension_label": label, "low": low, "high": high, "points": []}
            groups[group_key]["points"].append({"record": reference(record), "mean": sum(v / len(values) for v in values),
                                                "min": min(values), "max": max(values), "count": len(values),
                                                "_order": (record["inspection_date"], record["signed_at"] or "", record["id"])})
    output = []
    for group in groups.values():
        points = sorted(group.pop("points"), key=lambda p: p.pop("_order"))
        group.update(dimension_status(points, group["low"], group["high"]))
        group.update(total_count=len(points), points=points[-WINDOW:])
        output.append(group)
    priority = {"out_of_range": 0, "approaching": 1, "near_limit": 2, "insufficient": 3, "no_signal": 4}
    output.sort(key=lambda g: (priority[g["status"]], g["dept"], g["specification"], g["manufacturer"], g["field"], g["standard_id"]))
    return {"mode": "local_rules", "rule_version": RULE_VERSION, "dept": dept,
            "date_from": start.isoformat(), "date_to": end.isoformat(), "groups": output,
            "counts": dict(Counter(g["status"] for g in output)), "skipped": dict(skipped),
            "notes": ["仅观察钢板/钢卷已确认首检。同班组、生产单位、牌号、规格、标准版本和本单实际公差分别比较；临时偏差改变的公差单独成组。",
                      "每份表单作为一个点，展示该表单全部有效读数的均值、最小值和最大值；最近 5 份至少覆盖 3 个检验日期。复检不重复计数。",
                      "观察规则为均值连续向同一边界移动，累计变化至少为公差带宽的 10%，最新均值进入边界 10% 区域，且窗口内读数均未超差。",
                      "这些是可核对的初始观察规则，尚未经现场数据验证；不预测质量、不改变合格结论。当前读数没有独立样件与测点标识。"]}
