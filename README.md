# AI Hardware Research

Investment-research dashboard: the AI / semiconductor / robotics / optical & AR supply chain, end-to-end (ex-materials — raw materials live in the sibling [hard-assets-research](https://github.com/joeedessa/hard-assets-research) map).

**Live dashboard:** https://joeedessa.github.io/ai-hardware-research/

- `index.html` — the app (single-file, no build step)
- `data/*.json` — the research: 225 companies, chains, themes, catalysts, policy, froth lens, plus nightly machine data (quotes, indices, news, alerts, performance)
- `scripts/fetch_market.py` + `.github/workflows/refresh-data.yml` — the zero-cost nightly data robot
- `.github/workflows/ci.yml` — push-time validation (data JSON + app JS)
- `archive/` — frozen historical snapshots
