#!/usr/bin/env python3
"""Verify every external chart link the dashboard generates actually resolves.

Exists because broken symbol mappings are this project's most repeated bug and the
quietest one: a 404 chart link renders as a perfectly normal page. Three TradingView
exchange prefixes shipped wrong on inference and sat unnoticed until an unrelated
lookup exposed them; three more broke because a correction table was wired into the
Yahoo path only. See docs/qa-log.md class B.

Deliberately PARSES the mappings out of index.html rather than reimplementing them.
A checker that reimplements production logic drifts from it and then lies in both
directions — during this script's own development a hand-rolled port omitted the
Hong Kong zero-stripping rule and reported a false 404 on SMIC.

Usage:  python3 scripts/check_links.py [--yahoo] [--limit N]
Exit 1 if anything fails, so CI can gate on it.
"""
import argparse
import concurrent.futures as cf
import json
import pathlib
import re
import sys
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
UA = {'User-Agent': 'Mozilla/5.0 (compatible; ai-hardware-research link check)'}


def load_mappings(html):
    """Pull SYM_FIX and TV_EXCH straight out of the app so the two cannot diverge."""
    fix_src = re.search(r'const SYM_FIX=\{(.*?)\};', html, re.S)
    tv_src = re.search(r'const TV_EXCH=\{(.*?)\};', html, re.S)
    if not fix_src or not tv_src:
        sys.exit('could not locate SYM_FIX / TV_EXCH in index.html — did they get renamed?')
    sym_fix = dict(re.findall(r"'([^']+)':'([^']+)'", fix_src.group(1)))
    tv_exch = dict(re.findall(r"'(\.[A-Z]+)':'([A-Z]+)'", tv_src.group(1)))
    return sym_fix, tv_exch


def yahoo_symbol(t, sym_fix):
    t = sym_fix.get(t, t)
    m = re.match(r'^(\d+)\.HK$', t)
    if m:
        return m.group(1).zfill(4) + '.HK'
    if t.endswith('.SH'):
        return t[:-3] + '.SS'
    return t


def tv_symbol(t, sym_fix, tv_exch):
    t = sym_fix.get(t, t)
    m = re.match(r'^(.+?)(\.[A-Z]+)$', t)
    if not m:
        return None                      # bare US ticker: TradingView resolves it alone
    code, suf = m.groups()
    if suf == '.HK':
        code = str(int(code))            # HKEX drops leading zeros
    ex = tv_exch.get(suf)
    return f'{ex}-{code}' if ex else None


def status(url):
    req = urllib.request.Request(url, headers=UA, method='GET')
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception as e:                                   # DNS, TLS, timeout
        return f'ERR {type(e).__name__}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--yahoo', action='store_true',
                    help='also check Yahoo quote pages (slower, rate-limits more readily)')
    ap.add_argument('--limit', type=int, help='check only the first N records (smoke test)')
    args = ap.parse_args()

    html = (ROOT / 'index.html').read_text()
    sym_fix, tv_exch = load_mappings(html)
    rows = json.loads((ROOT / 'data' / 'companies.json').read_text())['companies']
    if args.limit:
        rows = rows[:args.limit]

    targets = []
    for r in rows:
        t = r['ticker']
        s = tv_symbol(t, sym_fix, tv_exch)
        if s:
            targets.append((t, 'tradingview', f'https://www.tradingview.com/symbols/{s}/'))
        elif '.' in t:
            print(f'  ! {t}: suffix has no TV_EXCH entry — chart link falls back to raw ticker')
        if args.yahoo:
            y = yahoo_symbol(t, sym_fix)
            targets.append((t, 'yahoo', f'https://finance.yahoo.com/quote/{y}'))

    print(f'checking {len(targets)} links across {len(rows)} records...')
    failures = []
    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        for (tk, venue, url), code in zip(targets, ex.map(lambda x: status(x[2]), targets)):
            if code != 200:
                failures.append((tk, venue, url, code))

    if failures:
        print(f'\n{len(failures)} BROKEN:')
        for tk, venue, url, code in failures:
            print(f'  {code}  {tk:<12} {venue:<12} {url}')
        print('\nA failure is usually one of two things: the exchange prefix in TV_EXCH is\n'
              'wrong for that venue, or our stored ticker names the wrong exchange entirely\n'
              '(check whether the security actually trades where the suffix claims).')
        return 1
    print(f'all {len(targets)} links OK')
    return 0


if __name__ == '__main__':
    sys.exit(main())
