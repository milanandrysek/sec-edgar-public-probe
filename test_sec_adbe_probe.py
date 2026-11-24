"""Synthetic values only. No network, no calls to live_read()."""
import copy
import json
import unittest
from pathlib import Path
from sec_adbe_probe import SPECS, FORMS, CIK, extract


def fixture():
    payload = {'cik': CIK, 'entityName': 'ADOBE INC.', 'facts': {'us-gaap': {}}}
    for i, (key, (tags, accn, start, end, unit)) in enumerate(SPECS.items(), 1):
        row = {'accn': accn, 'end': end, 'form': FORMS[accn], 'filed': '2026-09-22' if FORMS[accn] == '10-Q' else '2026-01-15', 'val': i*1_000_000}
        if start: row['start'] = start
        payload['facts']['us-gaap'].setdefault(tags[0], {'units': {}})['units'].setdefault(unit, []).append(row)
    return payload


def rows(p, key):
    tags, accn, start, end, unit = SPECS[key]
    return p['facts']['us-gaap'][tags[0]]['units'][unit]


def evaluate(p):
    return extract(p, mode='FIXTURE', source_sha256='synthetic-test')


class TestExtractor(unittest.TestCase):
    key = 'revenue_9m_2026'
    def setUp(self): self.p = fixture()
    def fact(self): return next(x for x in rows(self.p, self.key) if x.get('start') == '2025-11-29')
    def append(self, **kwargs):
        row = copy.deepcopy(self.fact()); row.update(kwargs); rows(self.p, self.key).append(row)
    def result(self): return evaluate(self.p)['fields'][self.key]
    def test_all_11(self):
        r = evaluate(self.p); self.assertEqual(r['status'], 'EXTRACTED_UNVERIFIED'); self.assertEqual(r['extracted'], 11)
        self.assertEqual([f['value'] for f in r['fields'].values()], list(range(1,12)))
    def test_duplicate(self):
        self.append(); self.assertEqual(self.result()['status'], 'EXTRACTED'); self.assertEqual(len(self.result()['evidence']), 1)
    def test_conflict(self):
        self.append(val=999000000); self.assertEqual(self.result()['status'], 'CONFLICT'); self.assertIsNone(self.result()['value'])
    def test_wrong_start(self):
        self.append(start='2026-05-30', val=999000000); self.assertEqual(self.result()['value'], 3)
    def test_wrong_end(self):
        self.append(end='2026-05-29', val=999000000); self.assertEqual(self.result()['value'], 3)
    def test_wrong_accession(self):
        self.append(accn='0000796343-26-000999', val=999000000); self.assertEqual(self.result()['value'], 3)
    def test_wrong_form(self):
        self.append(form='10-K', val=999000000); self.assertEqual(self.result()['value'], 3)
    def test_wrong_unit(self):
        row = copy.deepcopy(self.fact()); row['val'] = 999000000
        self.p['facts']['us-gaap']['Revenues']['units']['EUR'] = [row]; self.assertEqual(self.result()['value'], 3)
    def test_missing(self):
        rows(self.p, self.key).remove(self.fact()); self.assertEqual(self.result()['status'], 'MISSING'); self.assertIsNone(self.result()['value'])
    def test_boolean_rejected(self):
        self.fact()['val'] = True; self.assertEqual(self.result()['status'], 'INVALID')
    def test_string_rejected(self):
        self.fact()['val'] = '3000000'; self.assertEqual(self.result()['status'], 'INVALID')
    def test_cutoff(self):
        self.fact()['filed'] = '2026-12-01'; self.assertEqual(self.result()['status'], 'INVALID')
    def test_instant_not_duration(self):
        row = rows(self.p, 'cash_2026_08_28')[0]; row['start'] = '2026-05-30'; self.assertEqual(evaluate(self.p)['fields']['cash_2026_08_28']['status'], 'MISSING')
    def test_alias_conflict(self):
        row = copy.deepcopy(self.fact()); row['val'] = 999000000
        self.p['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax'] = {'units': {'USD': [row]}}
        self.assertEqual(self.result()['status'], 'CONFLICT')
    def test_equal_alias(self):
        self.p['facts']['us-gaap']['RevenueFromContractWithCustomerExcludingAssessedTax'] = {'units': {'USD': [copy.deepcopy(self.fact())]}}
        self.assertEqual(self.result()['value'], 3); self.assertEqual(len(self.result()['evidence']), 2)
    def test_zero_not_missing(self):
        self.fact()['val'] = 0; self.assertEqual(self.result()['status'], 'EXTRACTED'); self.assertEqual(self.result()['value'], 0)
    def test_negative_ebit(self):
        r = rows(self.p, 'ebit_fy2025')[0]; r['val'] = -2000000; self.assertEqual(evaluate(self.p)['fields']['ebit_fy2025']['value'], -2)
    def test_identity(self):
        self.p['cik'] = 1
        with self.assertRaises(ValueError): evaluate(self.p)
    def test_name(self):
        self.p['entityName'] = 'Another company'
        with self.assertRaises(ValueError): evaluate(self.p)
    def test_shares_positive(self):
        rows(self.p, 'diluted_weighted_average_shares_q3_2026')[0]['val'] = 0
        self.assertEqual(evaluate(self.p)['fields']['diluted_weighted_average_shares_q3_2026']['status'], 'INVALID')


if __name__ == '__main__': unittest.main(verbosity=2)
