"""Management exception workflow regressions on an isolated SQLite database."""
from contextlib import closing
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from flask import Flask, g, jsonify, request
from werkzeug.exceptions import HTTPException

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import db
import management_cases
import steel


class CaseChecks(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        settings = {"DATA_DIR":tmp.name,"DB_PATH":str(root/"test.db"),"AUDIO_DIR":str(root/"audio"),"REPORT_DIR":str(root/"reports")}
        replacement = patch.multiple(db, **settings)
        replacement.start()
        self.addCleanup(replacement.stop)
        db.init_db()
        self.app = Flask(__name__)
        self.app.testing = True

        def conn():
            if "c" not in g:
                g.c = db.connect()
            return g.c

        def me():
            return db.row(conn(), "SELECT * FROM users WHERE id=?", (request.headers.get("X-Test-User"),))

        def role(*roles):
            def decorate(fn):
                def wrapped(*args, **kwargs):
                    actor = me()
                    if not actor:
                        return jsonify(error="未登录"),401
                    if actor["role"] not in roles:
                        return jsonify(error="无权访问"),403
                    return fn(*args, **kwargs)
                wrapped.__name__ = fn.__name__
                return wrapped
            return decorate

        @self.app.teardown_appcontext
        def close(_):
            c = g.pop("c", None)
            if c:
                c.close()

        @self.app.errorhandler(HTTPException)
        def http_error(error):
            return jsonify(error=error.description),error.code

        management_cases.register(self.app,conn,me,role)
        self.client = self.app.test_client()
        with closing(db.connect()) as c:
            management_cases.init_schema(c)
            self.admin = self.user(c,"Admin","admin","管理组")
            self.sup = self.user(c,"Sup-A","supervisor","甲组")
            self.sup_b = self.user(c,"Sup-B","supervisor","乙组")
            self.worker = self.user(c,"Worker-A","inspector","甲组")
            self.other = self.user(c,"Other-A","inspector","甲组")
            self.worker_b = self.user(c,"Worker-B","inspector","乙组")
            self.failed = self.record(c,self.worker,"CASE-A")
            self.foreign = self.record(c,self.worker_b,"CASE-B")
            self.example = self.record(c,self.worker,"EXAMPLE",example=True)
            c.commit()
        self.headers = {"X-Test-User":str(self.sup)}

    def user(self,c,name,role,dept):
        uid = db.insert(c,"users",dict(emp_no=name,name=name,role=role,dept=dept,note=""))
        db.insert(c,"user_credentials",dict(user_id=uid,password_hash="test-only",is_admin=int(role=="admin"),enabled=1))
        return uid

    def record(self,c,owner,document,state="fail",parent=None,example=False,reject=False):
        data = steel.employee_example()
        data.update(document_no=document,is_example=example)
        if state=="fail":
            data["values"]["surface"]["value"]="不合格"
            data.update(qualified_quantity=0,rejected_quantity=1)
        if reject:
            data.update(disposition="拒收",additional_failure_confirmed=True)
        data=steel.normalize(data)
        return db.insert(c,"steel_records",dict(owner_id=owner,created_at="2026-11-10 09:00:00",updated_at="2026-11-10 09:00:00",
            version=2,status="final",data_json=json.dumps(data,ensure_ascii=False),evaluation_json=json.dumps(steel.evaluate(data),ensure_ascii=False),
            template_hash="fixture",snapshot="fixture",inspector_name="Worker",signed_at="2026-11-10 09:00:00",parent_id=parent))

    def request(self,method,path,body=None,uid=None):
        return getattr(self.client,method)(path,json=body,headers=self.headers if uid is None else {"X-Test-User":str(uid)})

    def path(self,rid=None,kind="steel"):
        return "/api/management/cases/%s/%d"%(kind,rid or self.failed)

    def update(self,version=0,**changes):
        body=dict(version=version,status="following",assignee_id=self.worker,due_date="2020-01-01",note="已安排复检")
        body.update(changes)
        return self.request("put",self.path(),body)

    def test_scope_example_exclusion_and_uncreated_cases(self):
        self.assertEqual(self.client.get("/api/management/cases").status_code,401)
        self.assertEqual(self.request("get","/api/management/cases",uid=self.worker).status_code,403)
        response=self.request("get","/api/management/cases").get_json()
        self.assertEqual([x["id"] for x in response["items"]],[self.failed])
        self.assertEqual(response["items"][0]["case"]["version"],0)
        self.assertEqual(response["summary"]["open"],1)
        self.assertNotIn("_data",response["items"][0])
        self.assertEqual(self.request("get",self.path(self.foreign)).status_code,404)
        self.assertEqual(self.request("put",self.path(self.foreign),dict(version=0,status="open",note="不允许")).status_code,404)
        self.assertEqual(self.request("get","/api/management/cases",uid=self.admin).get_json()["total"],2)

    def test_assignment_notification_history_cas_and_measurement_unchanged(self):
        with closing(db.connect()) as c:
            before=c.execute("SELECT data_json,status,version FROM steel_records WHERE id=?",(self.failed,)).fetchone()
            before=tuple(before)
        saved=self.update()
        self.assertEqual(saved.status_code,200,saved.get_json())
        item=saved.get_json()["item"]
        self.assertEqual((item["case"]["version"],item["case"]["status"],item["case"]["overdue"]),(1,"following",True))
        self.assertEqual(len(saved.get_json()["history"]),1)
        self.assertEqual(self.update().status_code,409)
        self.assertEqual(self.update(version=1,note="已确认下个班次执行").status_code,200)
        with closing(db.connect()) as c:
            self.assertEqual(tuple(c.execute("SELECT data_json,status,version FROM steel_records WHERE id=?",(self.failed,)).fetchone()),before)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM management_case_events").fetchone()[0],2)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM notifications WHERE user_id=?",(self.worker,)).fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM audit WHERE action='management_case_update'").fetchone()[0],2)

    def test_validation_rejects_invalid_owner_date_note_and_disabled_users(self):
        for changes in (dict(assignee_id=self.worker_b),dict(assignee_id=self.other),dict(due_date="2026-99-03"),dict(note=" "),dict(version=True),dict(status="unknown")):
            with self.subTest(changes=changes):
                self.assertEqual(self.update(**changes).status_code,400)
        with closing(db.connect()) as c:
            c.execute("UPDATE user_credentials SET enabled=0 WHERE user_id=?",(self.worker,));c.commit()
        self.assertEqual(self.update().status_code,400)
        self.assertEqual(self.update(assignee_id=self.sup).status_code,200)
        assignees=self.request("get",self.path()).get_json()["assignees"]
        self.assertEqual([u["id"] for u in assignees],[self.sup])

    def test_close_requires_real_descendant_pass_then_reopen_with_note(self):
        self.assertEqual(self.update(status="closed").status_code,409)
        with closing(db.connect()) as c:
            unrelated=self.record(c,self.worker,"OTHER-PASS",state="pass")
            c.commit()
        self.assertFalse(self.request("get",self.path()).get_json()["item"]["closure_allowed"])
        with closing(db.connect()) as c:
            child=self.record(c,self.worker,"CASE-A",state="pass",parent=self.failed)
            c.commit()
        result=self.update(status="closed",note="已核对复检结果及归档文件")
        self.assertEqual(result.status_code,200,result.get_json())
        item=result.get_json()["item"]
        self.assertTrue(item["has_passed_followup"])
        self.assertEqual(item["followup_record_id"],child)
        self.assertFalse(item["case"]["overdue"])
        self.assertEqual(self.update(version=1,status="open",note="继续跟踪该批后续使用情况").status_code,200)

    def test_recorded_rejection_allows_management_closure_with_original_failure(self):
        with closing(db.connect()) as c:
            reject=self.record(c,self.worker,"REJECTED",reject=True)
            c.commit()
        result=self.request("put",self.path(reject),dict(version=0,status="closed",note="已核对原单拒收处置",assignee_id=self.sup))
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertEqual(result.get_json()["item"]["state"],"fail")
        self.assertIn("拒收",result.get_json()["item"]["closure_reason"])

    def test_filters_and_pagination_cover_every_matching_failure(self):
        with closing(db.connect()) as c:
            for i in range(24):
                self.record(c,self.worker,"BULK-%03d"%i)
            c.commit()
        listing=self.request("get","/api/management/cases?q=BULK&page=3&page_size=10").get_json()
        self.assertEqual((listing["total"],listing["pages"],len(listing["items"])),(24,3,4))
        self.update()
        filtered=self.request("get","/api/management/cases?state=following&overdue=1&assignee_id=%d"%self.worker).get_json()
        self.assertEqual([r["id"] for r in filtered["items"]],[self.failed])
        self.assertEqual(self.request("get","/api/management/cases?page=0").status_code,400)
        self.assertEqual(self.request("get","/api/management/cases?state=not-real").status_code,400)

    def test_task_failure_follows_current_assignee_and_later_round_pass(self):
        with closing(db.connect()) as c:
            product=db.insert(c,"products",dict(code="CASE-TASK",name="工序测试产品"))
            plan=db.insert(c,"plans",dict(product_id=product,code="PLAN-CASE",status="open",workgroup="甲组"))
            template=db.insert(c,"form_templates",dict(code="TEST-FORM",revision="v1",title="工序测试",schema_json="{}",active=1))
            node=db.insert(c,"plan_nodes",dict(plan_id=plan,seq=1,name="工序测试",template_id=template))
            task=db.insert(c,"tasks",dict(plan_id=plan,node_id=node,seq=1,template_id=template,status="fail",assignee_id=self.other))
            def reading(round_no,state):
                return db.insert(c,"records",dict(task_id=task,round=round_no,operator_id=self.worker,created_at="2026-11-10 09:00:00",
                    template_id=template,template_revision="v1",values_json="{}",judge_json="{}",overall=state,snapshot="fixture"))
            failed=reading(1,"fail")
            c.commit()
        path=self.path(failed,kind="task")
        detail=self.request("get",path)
        self.assertEqual(detail.status_code,200,detail.get_json())
        self.assertIn(self.other,[r["id"] for r in detail.get_json()["assignees"]])
        value=dict(version=0,status="following",assignee_id=self.other,due_date="2026-12-01",note="交接给当前领取人复测")
        self.assertEqual(self.request("put",path,value).status_code,200)
        self.assertEqual(self.request("get",path,uid=self.sup_b).status_code,404)
        with closing(db.connect()) as c:
            reading(1,"pass")  # An old duplicate round is not a later reinspection.
            c.commit()
        self.assertFalse(self.request("get",path).get_json()["item"]["closure_allowed"])
        with closing(db.connect()) as c:
            passed=reading(2,"pass")
            c.commit()
        value.update(version=1,status="closed",note="已核对第二轮实测合格")
        result=self.request("put",path,value)
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertTrue(result.get_json()["item"]["has_passed_followup"])
        self.assertEqual(result.get_json()["item"]["followup_record_id"],passed)


if __name__=="__main__":
    unittest.main(verbosity=2)
