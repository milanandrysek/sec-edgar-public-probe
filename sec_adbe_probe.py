"""V3 Pb-10: fixed Adobe filing proof-of-concept, not a valuation.
Default: read an explicitly supplied local fixture. Live SEC request is opt-in.
No Google, broker, GitHub or other writes; emit JSON only to stdout.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
import sys
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

CIK = 796343
URL = 'https://data.sec.gov/api/xbrl/companyfacts/CIK0000796343.json'
Q = '0000796343-26-000156'
K = '0000796343-26-000003'
FORMS = {Q: '10-Q', K: '10-K'}
CUTOFF = '2026-11-24'
REV = ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet')
EBIT = ('OperatingIncomeLoss',)
# Candidate tags, not a claim that all aliases are equivalent in Adobe's filing.
# For this fixed experiment disagreement is explicit, never guessed or summed.
SPECS = {
    'revenue_fy2025': (REV, K, '2024-11-30', '2025-11-28', 'USD'),
    'ebit_fy2025': (EBIT, K, '2024-11-30', '2025-11-28', 'USD'),
    'revenue_9m_2026': (REV, Q, '2025-11-29', '2026-08-28', 'USD'),
    'ebit_9m_2026': (EBIT, Q, '2025-11-29', '2026-08-28', 'USD'),
    'revenue_9m_2025': (REV, Q, '2024-11-30', '2025-08-29', 'USD'),
    'ebit_9m_2025': (EBIT, Q, '2024-11-30', '2025-08-29', 'USD'),
    'cash_2026_08_28': (('CashAndCashEquivalentsAtCarryingValue',), Q, None, '2026-08-28', 'USD'),
    'short_term_investments_2026_08_28': (('ShortTermInvestments', 'ShortTermInvestmentsAvailableForSale', 'AvailableForSaleSecuritiesCurrent', 'MarketableSecuritiesCurrent'), Q, None, '2026-08-28', 'USD'),
    'current_debt_2026_08_28': (('LongTermDebtCurrent',), Q, None, '2026-08-28', 'USD'),
    'noncurrent_debt_2026_08_28': (('LongTermDebtNoncurrent',), Q, None, '2026-08-28', 'USD'),
    'diluted_weighted_average_shares_q3_2026': (('WeightedAverageNumberOfDilutedSharesOutstanding',), Q, '2026-05-30', '2026-08-28', 'shares'),
}


def evidence_key(row):
    return json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


def extract(payload, *, mode, source_sha256):
    if not isinstance(payload, dict) or str(payload.get('cik', '')).lstrip('0') != str(CIK):
        raise ValueError('CIK mismatch or absent')
    if str(payload.get('entityName', '')).strip().rstrip('.').upper() != 'ADOBE INC':
        raise ValueError('issuer mismatch or absent')
    taxonomy = payload.get('facts', {}).get('us-gaap')
    if not isinstance(taxonomy, dict):
        raise ValueError('us-gaap taxonomy unavailable')
    fields = {}
    for key, (tags, accession, start, end, unit) in SPECS.items():
        matching, invalid = [], []
        for tag in tags:
            for fact in taxonomy.get(tag, {}).get('units', {}).get(unit, []):
                if not isinstance(fact, dict):
                    continue
                if (fact.get('accn'), fact.get('start'), fact.get('end'), fact.get('form')) != (accession, start, end, FORMS[accession]):
                    continue
                val = fact.get('val')
                filed = fact.get('filed')
                valid_date = False
                if isinstance(filed, str):
                    try:
                        valid_date = datetime.strptime(filed, '%Y-%m-%d').date().isoformat() == filed and end <= filed <= CUTOFF
                    except ValueError:
                        pass
                row = {'taxonomy': 'us-gaap', 'tag': tag, 'unit': unit, 'accn': accession,
                       'start': start, 'end': end, 'form': fact.get('form'), 'filed': filed, 'raw_value': val,
                       'fy': fact.get('fy'), 'fp': fact.get('fp'), 'frame': fact.get('frame')}
                # Companyfacts provides numeric values, not localized strings.
                # Negative operating income and genuine zero are allowed.
                if not valid_date or isinstance(val, bool) or not isinstance(val, (int, float)) or not math.isfinite(val):
                    invalid.append(row)
                    continue
                if key.startswith('diluted_') and val <= 0:
                    invalid.append(row)
                    continue
                matching.append(row)
        unique = {evidence_key(r): r for r in matching}
        evidence = [unique[k] for k in sorted(unique)]
        values = {Decimal(str(r['raw_value'])) for r in evidence}
        reason = None
        if invalid:
            status, reason = 'INVALID', 'Matching context contains malformed value or filing date'
        elif not values:
            status, reason = 'MISSING', 'No permitted exact accession/period/form/unit/tag match'
        elif len(values) > 1:
            status, reason = 'CONFLICT', 'Multiple differing values; no automatic selection or averaging'
        else:
            status = 'EXTRACTED'
        value = float(next(iter(values)) / Decimal(1_000_000)) if status == 'EXTRACTED' else None
        fields[key] = {'status': status, 'value': value,
                       'units': 'million USD' if unit == 'USD' else 'million shares',
                       'expected_context': {'accn': accession, 'start': start, 'end': end, 'form': FORMS[accession], 'unit': unit},
                       'evidence': evidence, 'invalid_evidence': invalid, 'reason': reason}
    n = sum(f['status'] == 'EXTRACTED' for f in fields.values())
    return {'marker': 'V3_PB10', 'mode': mode, 'status': 'EXTRACTED_UNVERIFIED' if n == len(SPECS) else 'INCOMPLETE',
            'cik': CIK, 'issuer': payload['entityName'], 'source_url': URL if mode == 'LIVE' else None,
            'source_sha256': source_sha256, 'extracted': n, 'required': len(SPECS), 'fields': fields,
            'notice': 'Candidate tags and filing context still require independent statement-level acceptance. Fixed historical filings; no freshness, valuation or recommendation.'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def live_read():
    contact = os.environ.get('SEC_CONTACT_EMAIL', '').strip()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', contact) or not contact.isascii():
        raise ValueError('Set a valid separately authorized SEC_CONTACT_EMAIL before live mode')
    request = urllib.request.Request(URL, headers={'User-Agent': 'Independent research PoC ' + contact,
                                                  'Accept': 'application/json', 'Accept-Encoding': 'identity'})
    # No redirect, no retry, one request only; no credential or portfolio data.
    with urllib.request.build_opener(NoRedirect()).open(request, timeout=25) as response:
        if response.status != 200 or response.geturl() != URL:
            raise ValueError('unexpected HTTP response')
        data = response.read(30_000_001)
        if len(data) > 30_000_000:
            raise ValueError('response exceeds 30 MB bound')
    return data


def main():
    p = argparse.ArgumentParser(description=__doc__)
    modes = p.add_mutually_exclusive_group(required=True)
    modes.add_argument('--fixture', help='local synthetic JSON file')
    modes.add_argument('--live', action='store_true', help='one real HTTP read; separate approval required')
    a = p.parse_args()
    mode = 'LIVE' if a.live else 'FIXTURE'
    try:
        if a.live:
            data = live_read()
        else:
            with open(a.fixture, 'rb') as f:
                data = f.read(30_000_001)
            if len(data) > 30_000_000:
                raise ValueError('fixture exceeds 30 MB bound')
        def reject_constant(x):
            raise ValueError('nonfinite JSON constant: ' + x)
        result = extract(json.loads(data, parse_constant=reject_constant), mode=mode, source_sha256=hashlib.sha256(data).hexdigest())
        result['retrieved_at_utc'] = datetime.now(timezone.utc).isoformat()
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0 if result['status'] == 'EXTRACTED_UNVERIFIED' else 2
    except Exception as exc:
        print(json.dumps({'marker': 'V3_PB10', 'mode': mode, 'status': 'BLOCKED', 'error_type': type(exc).__name__, 'reason': str(exc), 'extracted': 0, 'required': len(SPECS)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
