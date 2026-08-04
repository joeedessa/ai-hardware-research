#!/usr/bin/env python3
"""Nightly market-data refresh for the AI Hardware Research dashboard.

Zero-cost pipeline: runs in GitHub Actions, pulls free data, commits JSON the
dashboard already knows how to fetch.
  - data/quotes.json  : price / 1d / 1mo / 52w-drawdown / 50-200DMA / mcap for
                        every ticker in companies.json (+ best-effort fwd P/E
                        for conviction-3 and froth-tagged names)
  - data/indices.json : SOX, SMH, SPY, NDX, KOSPI, TWSE, 10Y, FX
  - data/news.json    : Google News RSS headlines tagged by ticker

Failure policy: never clobber a good snapshot with a bad one — on wholesale
fetch failure the previous JSON files are left untouched.
"""
import json, os, re, sys, time
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), '..', 'data')

INDICES = {
    '^SOX': 'PHLX Semiconductor', 'SMH': 'VanEck Semis ETF', 'SPY': 'S&P 500 ETF',
    '^NDX': 'Nasdaq 100', '^KS11': 'KOSPI', '^TWII': 'Taiwan TAIEX',
    '^TNX': 'US 10Y yield', 'KRW=X': 'USD/KRW', 'TWD=X': 'USD/TWD', 'JPY=X': 'USD/JPY',
}

NEWS_QUERIES = [
    ('TSMC', ['TSM']), ('ASML', ['ASML']), ('SK Hynix HBM', ['000660.KS']),
    ('Nvidia AI', ['NVDA']), ('Broadcom AI chip', ['AVGO']), ('Micron HBM', ['MU']),
    ('CoWoS advanced packaging', ['TSM']), ('HBM4 memory', ['000660.KS', 'MU', '005930.KS']),
    ('co-packaged optics CPO', ['COHR', 'LITE']), ('Tesla Optimus robot', ['TSLA']),
    ('AI datacenter power', ['VRT', 'CEG', 'VST']), ('Vertiv liquid cooling', ['VRT']),
    ('semiconductor equipment orders', ['AMAT', 'KLAC', 'ASML']),
    ('Samsung foundry', ['005930.KS']), ('CoreWeave neocloud GPU', ['CRWV', 'NBIS']),
    ('chip export controls China', []), ('rare earth export controls', []),
    ('AI bubble valuation', []), ('humanoid robot supply chain', []),
]

# Long-form RSS from the leading independent semis/AI-trade voices (the free
# proxy for X: most of the accounts worth following publish here with open RSS).
VOICES = [
    ('SemiAnalysis', 'https://semianalysis.com/feed/'),
    ('Fabricated Knowledge', 'https://www.fabricatedknowledge.com/feed'),
    ('Citrini Research', 'https://www.citriniresearch.com/feed'),
    ('Irrational Analysis', 'https://irrationalanalysis.substack.com/feed'),
    ('Chips and Cheese', 'https://chipsandcheese.com/feed'),
    ('More Than Moore', 'https://morethanmoore.substack.com/feed'),
    ('The Chip Letter', 'https://thechipletter.substack.com/feed'),
    ('Asianometry', 'https://www.asianometry.com/feed'),
    ('ChipStrat', 'https://www.chipstrat.com/feed'),
]


SPECIAL = {
    '8299.TW': '8299.TWO',   # Phison — TPEx, not TWSE
    '3324.TW': '3324.TWO',   # Auras — TPEx
    'SHA.DE': 'SHA0.DE',     # Schaeffler post-2023 relisting symbol
}


def yahoo_symbol(t):
    """Map our ticker conventions to Yahoo's (HK 4-digit padding, Shanghai .SS)."""
    if t in SPECIAL:
        return SPECIAL[t]
    m = re.match(r'^(\d+)\.HK$', t)
    if m:
        return m.group(1).zfill(4) + '.HK'
    if t.endswith('.SH'):        # Shanghai (incl. STAR) is .SS on Yahoo
        return t[:-3] + '.SS'
    return t


def pct(a, b):
    return round((a / b - 1) * 100, 1) if a and b else None


def main():
    import yfinance as yf
    import pandas as pd

    with open(os.path.join(ROOT, 'companies.json')) as f:
        comp = json.load(f)['companies']
    tickers = [c['ticker'] for c in comp]
    ymap = {t: yahoo_symbol(t) for t in tickers}
    pe_targets = {c['ticker'] for c in comp if c.get('conviction') == 3 or c.get('froth')}

    now = datetime.now(timezone.utc).isoformat(timespec='seconds')
    errors = []

    # ── Price history for everything (one threaded batch) ──
    all_syms = sorted(set(ymap.values()) | set(INDICES))
    hist = yf.download(all_syms, period='1y', interval='1d', auto_adjust=True,
                       group_by='ticker', progress=False, threads=True)

    def metrics(sym):
        try:
            df = hist[sym].dropna(subset=['Close'])
            if len(df) < 5:
                return None
            close = df['Close']
            p = float(close.iloc[-1])
            out = {
                'p': round(p, 2),
                'd1': pct(p, float(close.iloc[-2])),
                'm1': pct(p, float(close.iloc[-22])) if len(close) >= 22 else None,
                'dd': round((p / float(close.max()) - 1) * 100, 1),
                'a50': round(float(close.tail(50).mean()), 2),
                'a200': round(float(close.tail(200).mean()), 2) if len(close) >= 200 else None,
            }
            return out
        except Exception:
            return None

    quotes = {}
    for t in tickers:
        m = metrics(ymap[t])
        if m:
            quotes[t] = m
        else:
            errors.append(t)

    # Stooq fallback for plain US tickers Yahoo missed (free CSV, no key)
    import urllib.request
    for t in list(errors):
        if not re.match(r'^[A-Z]+$', t):
            continue
        try:
            url = f'https://stooq.com/q/d/l/?s={t.lower()}.us&i=d'
            rows = urllib.request.urlopen(url, timeout=15).read().decode().strip().split('\n')[1:]
            closes = [float(r.split(',')[4]) for r in rows[-260:] if r.count(',') >= 4]
            if len(closes) >= 5:
                p = closes[-1]
                quotes[t] = {'p': round(p, 2), 'd1': pct(p, closes[-2]),
                             'm1': pct(p, closes[-22]) if len(closes) >= 22 else None,
                             'dd': round((p / max(closes) - 1) * 100, 1),
                             'a50': round(sum(closes[-50:]) / min(50, len(closes)), 2),
                             'a200': round(sum(closes[-200:]) / 200, 2) if len(closes) >= 200 else None,
                             'src': 'stooq'}
                errors.remove(t)
        except Exception:
            pass

    if len(quotes) < len(tickers) * 0.5:
        print(f'FATAL: only {len(quotes)}/{len(tickers)} quotes — keeping previous snapshot')
        sys.exit(1)

    # ── Market cap (fast_info) + best-effort forward P/E for the priority set ──
    for t in list(quotes):
        try:
            fi = yf.Ticker(ymap[t]).fast_info
            mc = getattr(fi, 'market_cap', None)
            if mc:
                quotes[t]['mc'] = int(mc)
        except Exception:
            pass
        if t in pe_targets:
            try:
                info = yf.Ticker(ymap[t]).info
                fpe = info.get('forwardPE')
                if fpe and 0 < fpe < 1000:
                    quotes[t]['fpe'] = round(fpe, 1)
                time.sleep(0.3)
            except Exception:
                pass

    with open(os.path.join(ROOT, 'quotes.json'), 'w') as f:
        json.dump({'_meta': {'as_of': now, 'source': 'Yahoo Finance (unofficial, via yfinance)',
                             'coverage': f'{len(quotes)}/{len(tickers)}', 'errors': errors},
                   'quotes': quotes}, f, separators=(',', ':'))
    print(f'quotes.json: {len(quotes)}/{len(tickers)} ({len(errors)} misses: {errors[:8]})')

    idx = {}
    for sym, name in INDICES.items():
        m = metrics(sym)
        if m:
            m['n'] = name
            idx[sym] = m
    with open(os.path.join(ROOT, 'indices.json'), 'w') as f:
        json.dump({'_meta': {'as_of': now}, 'symbols': idx}, f, separators=(',', ':'))
    print(f'indices.json: {len(idx)}/{len(INDICES)}')

    # ── Alerts: live data checked against the dashboard's own judgments ──
    froth = {c['ticker']: c.get('froth') for c in comp}
    conv = {c['ticker']: c.get('conviction') for c in comp}
    names = {c['ticker']: c['name'] for c in comp}
    alerts = []
    for t, q in quotes.items():
        dd, d1 = q.get('dd'), q.get('d1')
        if froth.get(t) == 1 and dd is not None and dd <= -15:
            alerts.append({'tk': t, 'type': 'window', 'val': dd,
                           'msg': f"{names[t]} ({t}) is {dd}% off its 52w high — insulated name past the entry threshold"})
        if froth.get(t) == 3 and dd is not None and dd >= -5:
            alerts.append({'tk': t, 'type': 'froth', 'val': dd,
                           'msg': f"{names[t]} ({t}) back within 5% of its 52w high — froth rebuilt"})
        if conv.get(t) == 3 and d1 is not None and abs(d1) >= 6:
            alerts.append({'tk': t, 'type': 'move', 'val': d1,
                           'msg': f"{names[t]} ({t}) moved {d1:+}% today — conviction-3 name, check the tape"})
    order = {'window': 0, 'move': 1, 'froth': 2}
    alerts.sort(key=lambda a: (order[a['type']], a['val']))
    with open(os.path.join(ROOT, 'alerts.json'), 'w') as f:
        json.dump({'_meta': {'as_of': now}, 'alerts': alerts}, f, separators=(',', ':'))
    print(f'alerts.json: {len(alerts)} alerts')

    # ── Performance scoreboard ──
    # Honesty rule: a judgment can only be scored from the date it was MADE.
    # Trailing returns of a basket picked today describe the past, they do not
    # test the framework — so they are reported separately and labelled.
    INCEPTION = {'conviction': '2026-05-30', 'froth': '2026-08-01'}

    def closes_of(sym):
        try:
            return [float(x) for x in hist[sym].dropna(subset=['Close'])['Close']]
        except Exception:
            return []

    def series_of(sym):
        try:
            df = hist[sym].dropna(subset=['Close'])
            return df.index, [float(x) for x in df['Close']]
        except Exception:
            return None, []

    def ret(cl, nd):
        return (cl[-1] / cl[-(nd + 1)] - 1) * 100 if len(cl) > nd else None

    def ret_since(sym, date_str):
        """Return % from the first trading day on/after date_str to the latest close."""
        idx, cl = series_of(sym)
        if idx is None or len(cl) < 2:
            return None
        try:
            start = pd.Timestamp(date_str)
            pos = idx.searchsorted(start)
            if pos >= len(cl) - 1:
                return None
            return (cl[-1] / cl[pos] - 1) * 100
        except Exception:
            return None

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 1) if vals else None

    def basket_trailing(tks):
        out = {}
        for label, nd in [('r1m', 21), ('r3m', 63), ('r6m', 126), ('r1y', 250)]:
            out[label] = avg([ret(closes_of(ymap.get(t, t)), nd) for t in tks])
        out['n'] = len(tks)
        return out

    def si(tks, date_str):
        return avg([ret_since(ymap.get(t, t), date_str) for t in tks])

    c3 = [t for t, v in conv.items() if v == 3]
    ins = [t for t, v in froth.items() if v == 1]
    hot = [t for t, v in froth.items() if v == 3]

    tracks = [
        {'name': 'Conviction ranking', 'inception': INCEPTION['conviction'],
         'note': 'Conviction scores were systematically re-ranked on 2026-05-30 (3 records revised 2026-08-04). Everything below is forward performance from that date.',
         'rows': [
             {'label': 'Conviction-3 chokepoints', 'n': len(c3), 'si': si(c3, INCEPTION['conviction'])},
             {'label': 'SMH — semis benchmark', 'n': 1, 'si': si(['SMH'], INCEPTION['conviction']), 'bench': True},
             {'label': 'SPY — market benchmark', 'n': 1, 'si': si(['SPY'], INCEPTION['conviction']), 'bench': True},
         ]},
        {'name': 'Froth lens', 'inception': INCEPTION['froth'],
         'note': 'Froth tags were written on 2026-08-01. This window is far too short to judge them — it is published so the record accumulates in the open rather than being claimed later.',
         'rows': [
             {'label': 'Insulated (froth 1)', 'n': len(ins), 'si': si(ins, INCEPTION['froth'])},
             {'label': 'High froth (froth 3)', 'n': len(hot), 'si': si(hot, INCEPTION['froth'])},
             {'label': 'SMH — semis benchmark', 'n': 1, 'si': si(['SMH'], INCEPTION['froth']), 'bench': True},
             {'label': 'SPY — market benchmark', 'n': 1, 'si': si(['SPY'], INCEPTION['froth']), 'bench': True},
         ]},
    ]

    baskets = {
        'Conviction-3 chokepoints': basket_trailing(c3),
        'Froth lens: insulated': basket_trailing(ins),
        'Froth lens: high-froth': basket_trailing(hot),
        'SMH (semis benchmark)': basket_trailing(['SMH']),
        'SPY (market benchmark)': basket_trailing(['SPY']),
    }
    with open(os.path.join(ROOT, 'performance.json'), 'w') as f:
        json.dump({'_meta': {'as_of': now,
                             'note': 'Equal-weight, price-only, local-currency; no rebalancing, dividends or FX.',
                             'retrospective_warning': 'The trailing table measures how TODAY\'S basket members performed in the past — it describes composition, not forecasting skill, because the tags did not exist for most of that window. Only the since-inception tracks test the framework.'},
                   'tracks': tracks, 'baskets': baskets}, f, separators=(',', ':'))
    print('performance.json tracks:', [(t['name'], t['inception'], t['rows'][0]['si']) for t in tracks])

    # ── News via Google News RSS ──
    try:
        import feedparser
        items, seen = [], set()
        for q, tks in NEWS_QUERIES:
            url = 'https://news.google.com/rss/search?q=' + q.replace(' ', '+') + '&hl=en-US&gl=US&ceid=US:en'
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:6]:
                    title = re.sub(r'\s+-\s+[^-]+$', '', e.title).strip()
                    key = title.lower()[:80]
                    if key in seen:
                        continue
                    seen.add(key)
                    src = getattr(getattr(e, 'source', None), 'title', '') or ''
                    dt = ''
                    if getattr(e, 'published_parsed', None):
                        dt = time.strftime('%Y-%m-%d', e.published_parsed)
                    items.append({'t': title, 'u': e.link, 's': src, 'd': dt, 'tk': tks, 'q': q, 'k': 'news'})
                time.sleep(0.2)
            except Exception as ex:
                print('feed fail:', q, ex)
        voices = []
        for name, url in VOICES:
            try:
                feed = feedparser.parse(url)
                for e in feed.entries[:4]:
                    dt = ''
                    if getattr(e, 'published_parsed', None):
                        dt = time.strftime('%Y-%m-%d', e.published_parsed)
                    summ = re.sub(r'<[^>]+>', '', getattr(e, 'summary', ''))[:220].strip()
                    voices.append({'t': e.title.strip(), 'u': e.link, 's': name, 'd': dt,
                                   'tk': [], 'k': 'voice', 'sum': summ})
                time.sleep(0.2)
            except Exception as ex:
                print('voice feed fail:', name, ex)
        voices.sort(key=lambda x: x['d'], reverse=True)
        items.sort(key=lambda x: x['d'], reverse=True)
        with open(os.path.join(ROOT, 'news.json'), 'w') as f:
            json.dump({'_meta': {'as_of': now, 'source': 'Google News RSS + curated long-form RSS (voices)'},
                       'items': items[:150], 'voices': voices[:40]}, f, separators=(',', ':'))
        print(f'news.json: {len(items[:150])} headlines + {len(voices[:40])} voice posts')
    except Exception as ex:
        print('news skipped:', ex)


if __name__ == '__main__':
    main()
