"""Exercise version pinning, deviations and repeat inspections in an isolated database."""
import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
from werkzeug.security import generate_password_hash
import app
import db
import steel


class StandardWorkflow(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p=Path(tmp.name)
        settings=patch.multiple(db,DATA_DIR=tmp.name,DB_PATH=str(p/"test.db"),
                                AUDIO_DIR=str(p/"audio"),REPORT_DIR=str(p/"reports"))
        settings.start();self.addCleanup(settings.stop)
        db.init_db()
        self.process=self.login("tech","process","A")
        self.inspect=self.login("inspect","inspector","A")
        self.other=self.login("other","process","B")

    def login(self, name, role, dept):
        password="test-only-password!"
        c=db.connect()
        uid=db.insert(c,"users",{"emp_no":name.upper(),"name":name,"role":role,"dept":dept})
        c.execute("INSERT INTO user_credentials(user_id,password_hash) VALUES(?,?)",
                  (uid,generate_password_hash(password,method="pbkdf2:sha256:1000")))
        c.commit();c.close()
        client=app.app.test_client()
        response=client.post("/api/login",json={"emp_no":name,"password":password})
        self.assertEqual(response.status_code,200,response.get_json())
        return client

    def new(self,sid=1):
        response=self.inspect.post("/api/steel/records",json={"standard_id":sid})
        self.assertEqual(response.status_code,201,response.get_json())
        return response.get_json()["record"]

    def test_new_and_pinned_versions_and_tamper(self):
        spec=dict(steel.blank_data(),code="A1",source="图纸 2026-A")
        spec["thickness_upper"]=0.26
        self.assertEqual(self.inspect.post("/api/steel/standards",json=spec).status_code,403)
        one=self.process.post("/api/steel/standards",json=spec).get_json()["standard"]
        rec=self.new(one["id"])
        self.assertEqual(rec["data"]["inspection_date"],steel.blank_data()["inspection_date"])
        spec.update(previous_revision=1,thickness_upper=0.24)
        two=self.process.post("/api/steel/standards",json=spec).get_json()["standard"]
        self.assertEqual(two["revision"],2)
        self.assertEqual(self.process.post("/api/steel/standards",json=spec).status_code,400)
        self.assertEqual(self.inspect.post("/api/steel/records",json={"standard_id":one["id"]}).status_code,400)
        url="/api/steel/records/"+str(rec["id"])
        loaded=self.inspect.get(url).get_json()["record"]
        self.assertEqual((loaded["standard_id"],loaded["data"]["thickness_upper"]),(one["id"],0.26))
        for changes in [{"width_nominal":1200},{"specification":"9*3000"},{"thickness_upper":5},
                        {"extra_rows":[{"name":"a","requirement":"changed","result":"x","verdict":"合格"}]}]:
            body={"version":rec["version"],"data":dict(rec["data"],**changes)}
            self.assertEqual(self.inspect.put(url,json=body).status_code,400)
            self.assertEqual(self.inspect.post(url+"/finalize",json=body).status_code,400)
        forged=dict(rec["data"],thickness_upper=15)
        self.assertEqual(self.inspect.post("/api/steel/records",json={"data":forged}).status_code,400)
        self.assertEqual(self.inspect.get(url).get_json()["record"]["version"],1)

    def test_per_record_deviation_and_empty_reinspection(self):
        rec=self.new()
        path="/api/steel/records/"+str(rec["id"])
        data=steel.employee_example()
        data.update(document_no="REAL-TEST",is_example=False)
        data["values"]["thickness"]["value"]=[6.5]
        blocked=self.inspect.post(path+"/finalize",json={"data":data,"version":rec["version"]})
        self.assertEqual(blocked.status_code,400,blocked.get_json())
        rec=blocked.get_json()["record"]
        limits={k:rec["data"][k] for k in ("thickness_upper","thickness_lower","width_lower","width_upper")}
        limits.update(thickness_upper=1.0,reason="本单临时图纸 B-07",version=rec["version"])
        applied=self.inspect.post(path+"/deviation",json=limits)
        self.assertEqual(applied.status_code,201,applied.get_json())
        rec=applied.get_json()["record"]
        did=rec["deviation"]["id"]
        self.assertEqual(self.other.get("/api/steel/deviations").get_json()["requests"],[])
        decision="/api/steel/deviations/%d/decision"%did
        body={"decision":"approved","note":"工艺会签 B-07/2"}
        self.assertEqual(self.other.post(decision,json=body).status_code,403)
        self.assertEqual(self.inspect.post(decision,json=body).status_code,403)
        self.assertEqual(self.process.post(decision,json=body).status_code,200)
        self.assertEqual(self.process.post(decision,json=body).status_code,409)
        loaded=self.inspect.get(path).get_json()["record"]
        self.assertEqual(loaded["data"]["thickness_upper"],1.0)
        self.assertEqual(loaded["deviation"]["reviewer"],"tech")
        self.assertEqual(self.inspect.post(path+"/finalize",json={"data":data,"version":rec["version"]}).status_code,409)
        final=self.inspect.post(path+"/finalize",json={"data":loaded["data"],"version":loaded["version"]})
        self.assertEqual(final.status_code,200,final.get_json())
        self.assertEqual(final.get_json()["record"]["evaluation"]["overall"],"pass")
        child=self.inspect.post(path+"/revise",json={}).get_json()["record"]
        self.assertEqual(self.inspect.post(path+"/revise",json={}).get_json()["record"]["id"],child["id"])
        self.assertEqual(child["parent_id"],rec["id"])
        self.assertEqual(child["data"]["values"],{})
        self.assertEqual(child["data"]["thickness_upper"],0.29)
        self.assertIsNone(child["deviation"])
        self.assertIsNone(child["data"]["qualified_quantity"])
        self.assertEqual(child["data"]["document_no"],"REAL-TEST")
        self.assertEqual(self.inspect.post("/api/steel/records/%d/finalize"%child["id"],
                         json={"version":child["version"],"data":child["data"]}).status_code,400)

    def test_unmatched_legacy_draft_and_malformed_date_cache(self):
        old=steel.blank_data()
        old["inspection_date"]="1999-01-01"
        response=self.inspect.post("/api/steel/records",json={"data":old}).get_json()["record"]
        self.assertEqual(response["data"]["inspection_date"],steel.blank_data()["inspection_date"])
        path="/api/steel/records/%d"%response["id"]
        legacy=copy.deepcopy(response["data"])
        legacy.update(document_no="inspect-old",thickness_upper=0.8)
        c=db.connect();c.execute("UPDATE steel_records SET standard_id=NULL,data_json=? WHERE id=?",
                                 (json.dumps(legacy),response["id"]));c.commit();c.close()
        self.assertEqual(self.inspect.post(path+"/finalize",json={"version":1,"data":legacy}).status_code,409)
        rebound=self.inspect.post(path+"/bind-standard",json={"version":1,"standard_id":1})
        self.assertEqual(rebound.status_code,200,rebound.get_json())
        self.assertEqual(rebound.get_json()["record"]["data"]["thickness_upper"],0.29)
        self.assertEqual(rebound.get_json()["record"]["data"]["document_no"],"inspect-old")

    def test_takeover_permitted_only_for_orphan_draft_in_own_team(self):
        r=self.new()
        path="/api/steel/records/%d"%r["id"]
        lead=self.login("lead","supervisor","A")
        unrelated=self.login("other-lead","supervisor","B")
        self.assertFalse(lead.get(path).get_json()["record"]["can_takeover"])
        self.assertEqual(lead.post(path+"/takeover",json={"version":1}).status_code,403)
        c=db.connect();c.execute("UPDATE user_credentials SET enabled=0 WHERE user_id=?",(r["owner_id"],));c.commit();c.close()
        self.assertEqual(unrelated.post(path+"/takeover",json={"version":1}).status_code,403)
        self.assertTrue(lead.get(path).get_json()["record"]["can_takeover"])
        response=lead.post(path+"/takeover",json={"version":1})
        self.assertEqual(response.status_code,200,response.get_json())
        self.assertNotEqual(response.get_json()["record"]["owner_id"],r["owner_id"])
        self.assertEqual(response.get_json()["record"]["version"],2)
        self.assertEqual(lead.post(path+"/takeover",json={"version":1}).status_code,403)


if __name__=="__main__":
    unittest.main(verbosity=2)
