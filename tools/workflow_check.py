"""Real API checks for scope, claim races, inspection limits and handoff. Temporary DB only."""
from concurrent.futures import ThreadPoolExecutor
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from werkzeug.security import generate_password_hash
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"backend"))
import app as server
import db
import seed
import identity


class WorkTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        paths=patch.multiple(db,DATA_DIR=tmp.name,DB_PATH=str(Path(tmp.name)/"test.db"),
                             AUDIO_DIR=str(Path(tmp.name)/"audio"),REPORT_DIR=str(Path(tmp.name)/"reports"))
        paths.start(); self.addCleanup(paths.stop)
        db.init_db(); c=db.connect();seed.seed_if_empty(c)
        hashed=generate_password_hash("fixture-password",method=identity.PASSWORD_METHOD)
        self.ids={}
        with c:
            for emp,role,group in (("ADMIN","admin","A"),("WORKER1","inspector","A"),("WORKER2","inspector","A"),
                                   ("B-STAFF","inspector","B"),("CHIEF","supervisor","A"),("TECH","process","A")):
                self.ids[emp]=identity._new_user(c,emp,emp,group,role,hashed,role=="admin")
        c.close()
        self.clients={}
        for emp in self.ids:
            client=server.app.test_client()
            self.assertEqual(200,client.post("/api/login",json={"emp_no":emp,"password":"fixture-password"}).status_code)
            self.clients[emp]=client
        plan=self.clients["ADMIN"].post("/api/work/plans",json={"name":"钢梁","code":"PLAN-TEST","workgroup":"A",
                                                               "predecessors":{"1":[],"2":[1],"3":[1,2]}})
        self.assertEqual(201,plan.status_code,plan.get_json())
        self.pid=plan.get_json()["plan_id"]
        self.plan="/api/plans/%d"%self.pid
        nodes=self.clients["WORKER1"].get(self.plan).get_json()["nodes"]
        self.urls=["/api/tasks/%d"%n["task_id"] for n in nodes]

    def test_permissions_race_and_handoff(self):
        first,_,last=self.urls
        a,b,sup=(self.clients[name] for name in ("WORKER1","WORKER2","CHIEF"))
        self.assertEqual([], self.clients["B-STAFF"].get("/api/bootstrap").get_json()["plans"])
        self.assertEqual(403,self.clients["B-STAFF"].get(first).status_code)
        self.assertEqual(403,self.clients["TECH"].post(first+"/claim",json={"version":1}).status_code)
        self.assertEqual(409,a.post(last+"/claim",json={"version":1}).status_code)
        self.assertEqual("open",a.get(first).get_json()["task"]["status"])
        with ThreadPoolExecutor(2) as pool:
            responses=list(pool.map(lambda client: client.post(first+"/claim",json={"version":1}), (a,b)))
        self.assertEqual([200,409],sorted(r.status_code for r in responses))
        winner,other=(a,b) if responses[0].status_code==200 else (b,a)
        task=winner.get(first).get_json()["task"]
        version=task["version"]
        self.assertEqual(403,other.post(first+"/records",json={"version":version,"values":{}}).status_code)
        self.assertEqual(400,sup.post(first+"/handoff",json={"version":version,"recipient":self.ids["B-STAFF"]}).status_code)
        other_uid=other.get("/api/me").get_json()["user"]["id"]
        self.assertEqual(200,sup.post(first+"/handoff",json={"version":version,"recipient":other_uid}).status_code)
        self.assertEqual(409,winner.post(first+"/records",json={"version":version,"values":{}}).status_code)
        values={f["key"]:{"value":v,"source":"manual"} for f,v in zip(seed.TPL_IQC["sections"][0]["fields"],["NaN",200,"H123456","齐全","合格"])}
        self.assertEqual(400,other.post(first+"/records",json={"version":version+1,"values":values}).status_code)
        values["plate_t"]["value"]=20
        passed=other.post(first+"/records",json={"version":version+1,"values":values})
        self.assertEqual(200,passed.status_code,passed.get_json())
        self.assertEqual("pass",passed.get_json()["task"]["status"])
        self.assertEqual(200,a.post(self.urls[1]+"/claim",json={"version":1}).status_code)
        self.assertEqual(409,a.post(last+"/claim",json={"version":1}).status_code)

    def test_parallel_plan_and_real_steps(self):
        admin=self.clients["ADMIN"]; a=self.clients["WORKER1"]
        config={"workgroup":"A","predecessors":{"1":[],"2":[],"3":[1,2]}}
        self.assertEqual(200,admin.post(self.plan+"/workflow",json=config).status_code)
        second=self.urls[1]; version=a.get(second).get_json()["task"]["version"]
        self.assertEqual(200,a.post(second+"/claim",json={"version":version}).status_code)
        steps=a.get("/api/bootstrap").get_json()["plans"][0]["steps"]
        self.assertEqual(["open","doing","open"],[s["status"] for s in steps])
        self.assertEqual(409,admin.post(self.plan+"/workflow",json=config).status_code)

    def test_recording_and_audit_scope(self):
        a=self.clients["WORKER1"]; b=self.clients["B-STAFF"]; admin=self.clients["ADMIN"]
        c=db.connect()
        db.audit(c, {"id":self.ids["WORKER1"],"name":"A"},"local_check","steel:123",{"test":"private-A"})
        c.close()
        self.assertNotIn("private-A",str(b.get("/api/audit?target=steel:123").get_json()))
        self.assertNotIn("private-A",str(b.get("/api/bootstrap").get_json()))
        audio=a.post("/api/asr/audio",data={"audio":(io.BytesIO(b"<html>invalid</html>"),"page.html")})
        self.assertEqual(400,audio.status_code,audio.get_json())
        self.assertEqual([],list(Path(db.AUDIO_DIR).iterdir()))
        self.assertEqual(404,admin.get("/api/audio/fake.html").status_code)
        self.assertNotEqual(200,a.post("/api/demo/reset",json={}).status_code)


if __name__=="__main__":
    unittest.main(verbosity=2)
