"""Report accuracy and conservative dimensional signals on disposable databases."""
from copy import deepcopy
from datetime import date
import json
import unittest
from unittest.mock import patch

import management_check as fixtures
import app as server
import identity
import management
import management_insights as insights


class InsightChecks(unittest.TestCase):
    setUpClass = classmethod(fixtures.ManagementChecks.setUpClass.__func__)
    setUp = fixtures.ManagementChecks.setUp
    steel_record = fixtures.ManagementChecks.steel_record
    task_round = fixtures.ManagementChecks.task_round
    get = fixtures.ManagementChecks.get

    def report(self, client=None, **args):
        with patch.object(insights, "today", return_value=date(2026, 11, 11)):
            response = (client or self.chief).get('/api/management/insights/report', query_string=args)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def dimensions(self, client=None, **args):
        with patch.object(insights, "today", return_value=date(2026, 11, 11)):
            response = (client or self.chief).get('/api/management/insights/dimensions', query_string=args)
        self.assertEqual(response.status_code, 200, response.get_json())
        return response.get_json()

    def readings(self, thickness, **kwargs):
        from steel import employee_example
        values = deepcopy(employee_example()['values'])
        values['thickness'] = dict(value=thickness if isinstance(thickness, list) else [thickness], source='manual', confirmed=True)
        return self.steel_record(values=values, **kwargs)

    def sequence(self, values, **kwargs):
        return [self.readings(value, day='2026-11-%02d' % (i+1), **kwargs) for i, value in enumerate(values)]

    def test_roles_scopes_and_direct_record_links(self):
        self.sequence([5.8, 5.86, 5.91, 5.96, 6.0])
        self.readings(5.7, owner='WORKER-B', document_no='PRIVATE-B', day='2026-11-11')
        self.steel_record('fail', owner='WORKER-B', day='2026-11-11')
        for endpoint in ('report', 'dimensions'):
            url = '/api/management/insights/' + endpoint
            self.assertEqual(server.app.test_client().get(url).status_code, 401)
            for account in ('WORKER-A', 'TECH-A'):
                self.assertEqual(self.clients[account].get(url).status_code, 403)
            for query in ({}, {'dept':'B'}):
                response = self.chief.get(url, query_string=query)
                self.assertEqual(response.status_code, 200)
                self.assertNotIn('PRIVATE-B', response.get_data(as_text=True))
        self.assertEqual(self.report(self.admin)['metrics']['final_count'], 2)
        report = self.report(period='week', date='2026-11-03')
        for team in report['teams']:
            for ref in team['records']:
                self.assertEqual(ref['url'], '#/management-record/' + ref['key'])
                self.assertEqual(self.chief.get('/api/management/records/' + ref['key'].replace(':','/')).status_code, 200)

    def test_reports_denominators_backlog_and_no_write(self):
        parent = self.steel_record('fail', day='2026-11-10')
        self.steel_record('pass', day='2026-11-11', parent=parent)
        self.steel_record('pass', day='2026-11-11')
        self.steel_record('draft', day='2026-10-01')
        self.steel_record('pass', day='2026-11-11', is_example=True)
        self.task_round('pending', day='2026-11-11')
        before = list(self.c.iterdump())
        result = self.report(date='2026-11-11')
        m = result['metrics']
        self.assertEqual((m['final_count'],m['first_inspection_count'],m['retest_count'],m['pending_count']), (2,1,1,1))
        self.assertEqual(result['first_pass_change_pp'], 100)
        self.assertEqual(len(result['todos']), 3)
        self.assertIn('核对合格复检', result['todos'][0]['action'])
        self.assertTrue(any(t['record']['date'] == '2026-10-01' for t in result['todos']))
        self.assertEqual(before, list(self.c.iterdump()))
        self.assertEqual(result['mode'], 'local_rules')

    def test_empty_and_calendar_weeks_include_equal_length_comparison(self):
        result = self.report(period='week')
        self.assertEqual((result['date_from'],result['date_to']), ('2026-11-09','2026-11-11'))
        self.assertEqual((result['previous_from'],result['previous_to']), ('2026-11-02','2026-11-04'))
        self.assertTrue(result['partial_week'])
        self.assertIsNone(result['first_pass_change_pp'])
        self.assertIsNone(result['metrics']['first_pass_rate'])
        old = self.report(period='week', date='2026-11-03')
        self.assertEqual(old['date_to'], '2026-11-08')
        self.assertFalse(old['partial_week'])
        with patch.object(insights, 'today', return_value=date(2026,11,11)):
            for endpoint, args in [('report',{'date':'2026-11-12'}),('report',{'date':'2026-02-30'}),
                                   ('report',{'period':'month'}),('report',{'date':'0001-01-01'}),
                                   ('dimensions',{'date_to':'0001-01-01'}),('dimensions',{'date_from':'2026-11-10','date_to':'2026-11-01'})]:
                self.assertEqual(self.chief.get('/api/management/insights/' + endpoint, query_string=args).status_code, 400)

    def test_superseded_pending_round_is_not_a_current_todo(self):
        self.task_round('pending', round_no=1, day='2026-11-10')
        self.task_round('pass', round_no=2, day='2026-11-11')
        self.assertEqual(self.report()['todos'], [])

    def test_sustained_movement_both_directions_and_evidence(self):
        ids = self.sequence([5.8,5.86,5.91,5.96,6.0])
        group = next(g for g in self.dimensions()['groups'] if g['field'] == 'thickness')
        self.assertEqual((group['status'],group['direction']), ('approaching','上限'))
        self.assertEqual([p['record']['id'] for p in group['points']], ids)
        self.sequence([5.72,5.66,5.60,5.55,5.50], manufacturer='另一生产厂')
        groups = self.dimensions()['groups']
        lower = next(g for g in groups if g['field'] == 'thickness' and g['manufacturer'] == '另一生产厂')
        self.assertEqual(lower['direction'], '下限')
        self.assertTrue(lower['enough_data'])

    def test_sparse_same_day_and_retest_do_not_fabricate_trends(self):
        root = self.readings(6.0)
        for _ in range(5):
            self.readings(6.01, parent=root)
        group = next(g for g in self.dimensions()['groups'] if g['field'] == 'thickness')
        self.assertEqual((group['status'],group['total_count']), ('insufficient',1))
        for value in [5.8,5.86,5.91,5.96,6.0]:
            self.readings(value)
        group = next(g for g in self.dimensions()['groups'] if g['field'] == 'thickness')
        self.assertEqual(group['status'], 'insufficient')
        self.assertEqual(group['day_count'], 1)

    def test_noisy_series_and_raw_extremes_are_not_hidden_by_means(self):
        self.sequence([5.8,5.96,5.9,5.96,6.0])
        group = next(g for g in self.dimensions()['groups'] if g['field'] == 'thickness')
        self.assertEqual(group['status'], 'near_limit')
        self.readings([5.5,6.1], state='fail', day='2026-11-06')
        group = next(g for g in self.dimensions()['groups'] if g['field'] == 'thickness')
        self.assertEqual(group['status'], 'out_of_range')
        self.assertAlmostEqual(group['points'][-1]['mean'], 5.8)

    def test_standard_versions_effective_tolerances_and_invalid_data_are_separate(self):
        self.sequence([5.8,5.86,5.91,5.96,6.0])
        actor = identity.active_user(self.c, self.ids['CHIEF-A'])
        records = management.visible_records(self.c, actor)
        records[0]['standard']['version'] = 'other-version'
        records[1]['_data']['thickness_upper'] = .4
        records[2]['_data']['values']['thickness']['value'] = [float('nan')]
        records[3]['_data']['values']['thickness'] = dict(value=[5.9], source='asr', confirmed=False)
        result = insights.dimensions(records, actor, {}, current_day=date(2026,11,11))
        thickness = [g for g in result['groups'] if g['field'] == 'thickness']
        self.assertEqual(len(thickness), 3)
        self.assertTrue(all(g['status'] == 'insufficient' for g in thickness))
        self.assertEqual(result['skipped']['无效或未确认读数项目'], 2)
        json.dumps(result, allow_nan=False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
