#!/usr/bin/env python3
"""Nightly market-data refresh for the AI Hardware Research dashboard.

Zero-cost pipeline: runs in GitHub Actions, pulls free data, commits JSON the
widget already knows how to fetch.
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
                    items.append({'t': title, 'u': e.link, 's': src, 'd': dt, 'tk': tks, 'q': q})
                time.sleep(0.2)
            except Exception as ex:
                print('feed fail:', q, ex)
        items.sort(key=lambda x: x['d'], reverse=True)
        with open(os.path.join(ROOT, 'news.json'), 'w') as f:
            json.dump({'_meta': {'as_of': now, 'source': 'Google News RSS'},
                       'items': items[:150]}, f, separators=(',', ':'))
        print(f'news.json: {len(items[:150])} headlines')
    except Exception as ex:
        print('news skipped:', ex)


if __name__ == '__main__':
    main()
