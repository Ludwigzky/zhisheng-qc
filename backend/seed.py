# -*- coding: utf-8 -*-
"""产品、检验计划（ITP）和已配置的工序模板。账号由本机部长邀请开通。"""

PRODUCTS = [
    {"code": "WH2026-0111", "name": "桥式起重机", "model": "LD10t-22.5m", "customer": "中车洛阳机车",
     "contract_no": "HT-2026-0071", "note": "主梁双梁箱型结构，跨度 22.5 m"},
    {"code": "WH2026-0112", "name": "门式起重机", "model": "MG20/5t-30m", "customer": "河南卫华重工装备",
     "contract_no": "HT-2026-0073", "note": "双主梁门式，跨度 30 m"},
]

TPL_IQC = {
    "code": "T-IQC-01", "revision": "v1.3", "title": "来料检验记录（IQC）",
    "std_version": "GB/T 700-2006 / GB/T 11263-2017",
    "spec_ref": "技术协议 TA-2026-011 v3 · 第 4.2 条",
    "sections": [
        {"name": "原材料", "fields": [
            {"key": "plate_t", "label": "钢板厚度 t", "syn": "钢板厚度/板厚/t", "unit": "mm",
             "nominal": 20, "tol": 0.8, "decimals": 1, "required": True},
            {"key": "flange_b", "label": "型钢翼缘宽度 b", "syn": "翼缘宽度/翼缘/b", "unit": "mm",
             "min": 198, "max": 202, "decimals": 0, "required": True},
            {"key": "heat_no", "label": "钢板炉批号", "syn": "炉批号/批号", "type": "text",
             "hint": "与质保书一致，6~20 位", "required": True},
            {"key": "mill_cert", "label": "材质证明书", "syn": "材质证明/质保书", "type": "enum",
             "options": ["齐全", "缺失"], "pass": ["齐全"], "required": True},
            {"key": "surface", "label": "外观质量", "syn": "外观/表面质量", "type": "enum",
             "options": ["合格", "轻微锈蚀", "严重锈蚀"], "pass": ["合格"], "required": True},
        ]},
    ],
    "rules": [],
}

TPL_IPQC = {
    "code": "T-IPQC-02", "revision": "v2.0", "title": "主梁过程检验记录（IPQC）",
    "std_version": "GB/T 3811-2008 / GB 50278-2010",
    "spec_ref": "技术协议 TA-2026-011 v3 · 第 5.1~5.4 条",
    "sections": [
        {"name": "几何精度", "fields": [
            {"key": "span", "label": "跨度实测 L", "syn": "跨度/跨度实测/L", "unit": "mm",
             "nominal": 22500, "tol": 3, "decimals": 0, "required": True},
            {"key": "diag_l1", "label": "对角线 L1", "syn": "对角线L1/对角线一/对角线1", "unit": "mm",
             "nominal": 22500, "tol": 4, "decimals": 0, "required": True},
            {"key": "diag_l2", "label": "对角线 L2", "syn": "对角线L2/对角线二/对角线2", "unit": "mm",
             "nominal": 22500, "tol": 4, "decimals": 0, "required": True},
            {"key": "camber", "label": "主梁上拱度 F", "syn": "上拱度/拱度/F", "unit": "mm",
             "min": 24.5, "max": 60, "decimals": 1, "required": True},
            {"key": "level", "label": "水平度", "syn": "水平度/水平", "unit": "mm/m",
             "min": 0, "max": 0.5, "decimals": 2, "required": True},
            {"key": "web_vert", "label": "腹板垂直度", "syn": "腹板垂直度/垂直度", "unit": "mm",
             "min": 0, "max": 2, "decimals": 1, "required": True},
        ]},
        {"name": "焊缝与涂装", "fields": [
            {"key": "weld", "label": "焊缝外观", "syn": "焊缝/焊缝外观", "type": "enum",
             "options": ["合格", "咬边", "气孔", "裂纹"], "pass": ["合格"], "required": True},
            {"key": "film", "label": "涂装膜厚", "syn": "膜厚/涂装膜厚", "unit": "μm",
             "min": 120, "max": 400, "decimals": 0, "required": True},
        ]},
    ],
    "rules": [
        {"key": "diag_delta", "label": "对角线差 Δ", "syn": "对角线差/对角差",
         "expr": "abs(diag_l1-diag_l2)", "unit": "mm", "min": 0, "max": 3, "decimals": 1,
         "section": "计算结果"},
    ],
}

TPL_FQC = {
    "code": "T-FQC-03", "revision": "v1.1", "title": "出厂检验记录（FQC）",
    "std_version": "GB/T 23723.1-2009 / GB 6067.1-2010",
    "spec_ref": "技术协议 TA-2026-011 v3 · 第 7.1~7.6 条",
    "sections": [
        {"name": "性能试验", "fields": [
            {"key": "hoist_h", "label": "起升高度", "syn": "起升高度/提升高度", "unit": "mm",
             "nominal": 12000, "tol": 10, "decimals": 0, "required": True},
            {"key": "static_load", "label": "静载试验载荷", "syn": "静载/静载试验/静载载荷", "unit": "kg",
             "min": 12500, "max": 13000, "decimals": 0, "required": True},
            {"key": "dyn_load", "label": "动载试验载荷", "syn": "动载/动载试验/动载载荷", "unit": "kg",
             "min": 11000, "max": 11500, "decimals": 0, "required": True},
            {"key": "brake_slip", "label": "制动下滑量", "syn": "制动下滑量/下滑量/制动", "unit": "mm",
             "min": 0, "max": 2.5, "decimals": 1, "required": True},
        ]},
        {"name": "电气与安全", "fields": [
            {"key": "ins_res", "label": "绝缘电阻", "syn": "绝缘电阻/绝缘", "unit": "MΩ",
             "min": 1.0, "max": 100, "decimals": 1, "required": True},
            {"key": "gnd_res", "label": "接地电阻", "syn": "接地电阻/接地", "unit": "Ω",
             "min": 0.0, "max": 4.0, "decimals": 2, "required": True},
            {"key": "limit_ok", "label": "限位可靠性", "syn": "限位/限位可靠性", "type": "enum",
             "options": ["可靠", "不可靠"], "pass": ["可靠"], "required": True},
        ]},
    ],
    "rules": [],
}

PLAN_NODES = [
    {"seq": 1, "name": "来料检验（IQC）", "short_name": "来料", "template": TPL_IQC},
    {"seq": 2, "name": "主梁过程检验（IPQC）", "short_name": "过程", "template": TPL_IPQC},
    {"seq": 3, "name": "出厂检验（FQC）", "short_name": "出厂", "template": TPL_FQC},
]


def seed_if_empty(conn):
    from db import insert, rows, now
    import json

    if rows(conn, "SELECT id FROM form_templates LIMIT 1"):
        return False

    for p in PRODUCTS:
        insert(conn, "products", p)

    tpl_ids = {}
    for t in (TPL_IQC, TPL_IPQC, TPL_FQC):
        tid = insert(conn, "form_templates", {
            "code": t["code"], "revision": t["revision"], "title": t["title"],
            "std_version": t["std_version"], "spec_ref": t["spec_ref"],
            "schema_json": json.dumps(t, ensure_ascii=False), "created_at": now(), "active": 1,
        })
        tpl_ids[t["code"]] = tid

    prods = rows(conn, "SELECT * FROM products ORDER BY id")
    for prod in prods:
        pid = insert(conn, "plans", {
            "product_id": prod["id"],
            "code": "ITP-%s-01" % prod["code"],
            "std_ref": "GB/T 3811-2008 / 技术协议 TA-2026-011 v3",
            "status": "open", "created_at": now(),
        })
        for n in PLAN_NODES:
            t = n["template"]
            tid = tpl_ids[t["code"]]
            nid = insert(conn, "plan_nodes", {
                "plan_id": pid, "seq": n["seq"], "name": n["name"],
                "short_name": n["short_name"], "template_id": tid,
            })
            insert(conn, "tasks", {
                "plan_id": pid, "node_id": nid, "seq": n["seq"], "template_id": tid,
                "template_revision": t["revision"], "status": "open",
                "assignee_id": None, "claimed_at": None, "done_at": None, "latest_record_id": None,
            })
    conn.commit()
    return True
