# Known issues (Python baseline)

| ID | Area | Issue | Source |
|---|---|---|---|
| KI-1 | Security | SEC-1…SEC-7 | SECURITY_REVIEW.md |
| KI-2 | GUI | Pillow icon and logo decoding runs on the Tk thread at startup (low impact) | `gui/app.py:341`, `gui/assets.py:64,76` |
| KI-3 | GUI | Remaining roadmap items: queue copy and membership scans, widget and font startup cost, output and cookie preparation | `docs/OPTIMIZATION_ROADMAP.md` |
| KI-4 | GUI (suspected) | History and library loading when a panel opens may block | `gui/final_behaviors.py:315`; needs measurement |
| KI-5 | Memory | Report models grow with the result count; cache JSON merge scales with index size | OPTIMIZATION_ROADMAP |
| KI-6 | Network | No single retry budget across connection, body, auth and backend recovery | OPTIMIZATION_ROADMAP |
| KI-7 | Structure | Global mutable compatibility layer (`compat.py`, `runtime/surface.py`) | audit |
| KI-8 | Tests | 21 tests are skipped (GUI, Playwright and live). Browser and GUI paths have no automated baseline | ENVIRONMENT.md |
