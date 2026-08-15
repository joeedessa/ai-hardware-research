# AI Hardware Research

Investment-research dashboard: the AI / semiconductor / robotics / optical & AR supply chain, end-to-end (ex-materials — raw materials live in the sibling [hard-assets-research](https://github.com/joeedessa/hard-assets-research) map).

**Live dashboard:** https://joeedessa.github.io/ai-hardware-research/

- `index.html` — the app (single-file, no build step)
- `data/*.json` — the research: 225 companies, chains, themes, catalysts, policy, froth lens, plus nightly machine data (quotes, indices, news, alerts, performance)
- `scripts/fetch_market.py` + `.github/workflows/refresh-data.yml` — the zero-cost nightly data robot
- `.github/workflows/ci.yml` — push-time validation (data JSON + app JS)
- `archive/` — frozen historical snapshots
- `docs/qa-log.md` — running record of bugs, root causes and the guards that now
  prevent them, plus a pre-flight checklist. Read §1 before committing; append to
  §4 when something breaks.

## License & disclaimer

© 2026 Joe Edessa. All rights reserved. This repository is public for personal-hosting convenience — **no license is granted** for republication or commercial reuse of the research content or code. Personal investment research, **not investment advice**. Referenced analyst materials are cited in `data/sources.json` and remain the property of their authors (and are not stored in this repo). Market data comes from free public feeds and is not guaranteed accurate.
