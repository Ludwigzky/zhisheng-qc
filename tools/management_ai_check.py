"""API keys, billing triggers, scoped payloads, fallback and cache isolation; no live API calls."""
from contextlib import closing
from datetime import date
import io
import json
import unittest
import urllib.error
from unittest.mock import patch

import management_check as fixtures
import app as server
import db
import management_ai as ai
import management_insights as insights


KEY = 'isolated-fake-api-key-123456'


def response(ref='R1'):
    return json.dumps({'summary':'本期检验情况已汇总。',
        'highlights':[{'text':'建议核对本期检验与复检记录。','refs':[ref], 'team_refs':['T1']}],
        'actions':[{'text':'请核对待办及处置记录。','refs':[], 'team_refs':[]}],
        'limitations':'数据为当前记录汇总，建议结合原单核对。'}, ensure_ascii=False)


class AIChecks(unittest.TestCase):
    setUpClass = classmethod(fixtures.ManagementChecks.setUpClass.__func__)
    setUp = fixtures.ManagementChecks.setUp
    steel_record = fixtures.ManagementChecks.steel_record

    def configure(self, **updates):
        current = self.admin.get('/api/admin/ai-config').get_json()
        body = dict(version=current['version'],enabled=True,base_url=ai.DEFAULT_URL,model=ai.DEFAULT_MODEL,timeout=25,api_key=KEY)
        body.update(updates)
        result = self.admin.put('/api/admin/ai-config',json=body)
        self.assertEqual(result.status_code,200,result.get_json())
        self.assertNotIn(KEY,result.get_data(as_text=True))
        return result.get_json()

    def generate(self, client=None):
        result = (client or self.chief).post('/api/management/insights/generate',json={'date':'2026-11-01'})
        self.assertEqual(result.status_code,200,result.get_json())
        return result.get_json()

    def test_config_permissions_encryption_blank_key_cas_and_delete(self):
        url='/api/admin/ai-config'
        for method in ('get','put'):
            self.assertEqual(getattr(server.app.test_client(),method)(url,json={}).status_code,401)
            for account in ('CHIEF-A','WORKER-A','TECH-A'):
                self.assertEqual(getattr(self.clients[account],method)(url,json={}).status_code,403)
        config=self.configure()
        encrypted=db.row(self.c,'SELECT * FROM ai_config WHERE id=1')['encrypted_key']
        self.assertNotIn(KEY,encrypted)
        self.assertEqual(ai.secret(ai.settings(self.c)),KEY)
        self.configure(api_key='',model='other-model')
        self.assertEqual(ai.secret(ai.settings(self.c)),KEY)
        self.assertEqual(self.admin.put(url,json=dict(version=config['version']+1,enabled=True,base_url='https://example.com',api_key='')).status_code,400)
        self.assertEqual(self.admin.put(url,json=dict(version=config['version'],enabled=False)).status_code,409)
        removed=self.configure(remove_key=True,api_key='')
        self.assertFalse(removed['enabled'])
        self.assertFalse(removed['key_saved'])
        self.assertNotIn(KEY,json.dumps(db.rows(self.c,'SELECT detail FROM audit')))

    def test_config_validation_and_no_key_cannot_enable(self):
        for values in ({'enabled':True,'api_key':''},{'base_url':'http://api.deepseek.com'},
                       {'base_url':'https://localhost'},{'base_url':'https://127.0.0.1'},
                       {'base_url':'https://api.deepseek.com/?key=secret'}, {'base_url':'https://key@api.deepseek.com'},
                       {'base_url':'https://api.deepseek.com/arbitrary-path'}, {'timeout':100}, {'model':'bad\nmodel'}):
            initial=self.admin.get('/api/admin/ai-config').get_json()
            body=dict(version=initial['version'],enabled=True,api_key=KEY)
            body.update(values)
            self.assertEqual(self.admin.put('/api/admin/ai-config',json=body).status_code,400)

    def test_get_never_bills_explicit_post_caches_and_masks_payload(self):
        self.steel_record(day='2026-11-01',document_no='DOC-PRIVATE',manufacturer='PRIVATE-FACTORY')
        self.steel_record(owner='WORKER-B',document_no='B-SECRET')
        self.configure()
        with patch.object(ai,'transport',return_value=response()) as call:
            first=self.chief.get('/api/management/insights/report?date=2026-11-01').get_json()
            self.assertEqual(first['ai']['status'],'ready')
            call.assert_not_called()
            result=self.generate()
            self.assertEqual(result['mode'],'ai_api')
            self.assertEqual(result['ai']['status'],'generated')
            outgoing=call.call_args.args[2]
            text=json.dumps(outgoing,ensure_ascii=False)
            for hidden in ('DOC-PRIVATE','PRIVATE-FACTORY','WORKER-A','B-SECRET',KEY):
                self.assertNotIn(hidden,text)
            self.assertEqual(result['ai']['highlights'][0]['records'][0]['document_no'],'DOC-PRIVATE')
            cached=self.generate()
            self.assertEqual(cached['ai']['status'],'cached')
            self.chief.get('/api/management/insights/report?date=2026-11-01')
            self.assertEqual(call.call_count,1)
            # Identical metrics in another team must not reuse this actor's report.
            other=self.generate(self.clients['CHIEF-B'])
            self.assertEqual(call.call_count,2)
            self.assertNotIn('DOC-PRIVATE',json.dumps(other))

    def test_api_failures_and_unknown_references_fall_back_without_changing_numbers(self):
        self.steel_record()
        self.configure()
        for failure in (ai.AIError('API超时'), response('R999'), '{"summary": "'+KEY+'"}'):
            self.configure(api_key='')
            with patch.object(ai,'transport',side_effect=failure if isinstance(failure,Exception) else None,return_value=failure):
                result=self.generate()
            self.assertEqual(result['ai']['status'],'fallback')
            self.assertEqual(result['metrics']['final_count'],1)
            self.assertNotIn(KEY,json.dumps(result))

    def test_permission_and_record_scope_changes_during_call_discard_old_report(self):
        self.steel_record(document_no='BECOMES-PRIVATE')
        self.configure()
        def move_record(*args):
            with closing(db.connect()) as changed, changed:
                changed.execute('UPDATE users SET dept=? WHERE id=?',('B',self.ids['WORKER-A']))
            return response()
        with patch.object(ai,'transport',side_effect=move_record):
            result=self.generate()
        self.assertEqual(result['ai']['status'],'fallback')
        self.assertEqual(result['metrics']['final_count'],0)
        self.assertNotIn('BECOMES-PRIVATE',json.dumps(result))
        self.configure(api_key='')
        def disable_actor(*args):
            with closing(db.connect()) as changed, changed:
                changed.execute('UPDATE user_credentials SET enabled=0 WHERE user_id=?',(self.ids['CHIEF-A'],))
            return response()
        with patch.object(ai,'transport',side_effect=disable_actor):
            result=self.chief.post('/api/management/insights/generate',json={'date':'2026-11-01'})
        self.assertEqual(result.status_code,403)
        self.assertNotIn('metrics',result.get_json())

    def test_test_connection_uses_no_business_data(self):
        self.configure()
        with patch.object(ai,'transport',return_value='{"ok":true}') as call:
            result=self.admin.post('/api/admin/ai-config/test',json={})
        self.assertEqual(result.status_code,200)
        self.assertNotIn('metrics',json.dumps(call.call_args.args[2]))
        self.assertEqual(self.chief.post('/api/admin/ai-config/test',json={}).status_code,403)

    def test_http_protocol_and_provider_errors_do_not_expose_keys(self):
        config=dict(base_url=ai.DEFAULT_URL,model=ai.DEFAULT_MODEL,timeout=25)
        valid={'choices':[{'finish_reason':'stop','message':{'content':'{"ok":true}'}}]}
        class Reply(io.BytesIO):
            pass
        with patch.object(ai.socket,'getaddrinfo',return_value=[(0,0,0,'',('1.1.1.1',443))]), patch.object(ai.urllib.request,'build_opener') as factory:
            factory.return_value.open.return_value=Reply(json.dumps(valid).encode())
            self.assertEqual(ai.transport(config,KEY,[]),'{"ok":true}')
            req=factory.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,'https://api.deepseek.com/chat/completions')
            self.assertEqual(req.headers['Authorization'],'Bearer '+KEY)
            self.assertEqual(json.loads(req.data)['response_format'],{'type':'json_object'})
            factory.return_value.open.side_effect=urllib.error.HTTPError('url',401,'provider '+KEY,{},None)
            with self.assertRaises(ai.AIError) as caught:
                ai.transport(config,KEY,[])
            self.assertNotIn(KEY,str(caught.exception))


if __name__=='__main__':
    unittest.main(verbosity=2)
