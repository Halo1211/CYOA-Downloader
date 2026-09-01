# JavaScript Website Archive Guide

This guide explains archive strategies, crawl limits, output structure, and
operational safeguards for CYOA websites that expose content after JavaScript
runs.

## Choose a strategy

| Strategy | Behavior | Recommended use |
| --- | --- | --- |
| Classic | Downloads the entry page and statically discoverable assets | Standard ICC projects and maximum historical compatibility |
| Smart | Classic plus a bounded same-origin story-route crawl | Choices navigate to distinct URLs or pages |
| Browser | Smart plus runtime response and lazy-load capture | SPAs, Next.js, runtime arrays, and JavaScript-only assets |
| Auto | Profiles the target and selects a structured adapter or strategy | Default choice for an unfamiliar URL |

Classic remains available as the historical baseline. Auto, Smart, and Browser
are bounded additions rather than unrestricted crawlers.

## GUI setup

1. Choose **Pure Website Folder** for a custom site without normal ICC project
   data.
2. Open **Settings → JavaScript Archive Policy**.
3. Choose **Auto**, or an explicit strategy for diagnosis.
4. Set **Maximum pages** (`1..5000`) and **Maximum depth** (`0..100`).
5. Select **Safe** or **Off** interaction and configure runtime limits.
6. Save the policy and start the queue normally.

Every number is a safety cap, not a work target. Crawling and runtime capture
stop early when they no longer discover useful routes, assets, or responses.

## Settings reference

| Option | Purpose | Guidance |
| --- | --- | --- |
| Auto | Profiles project data, CYOA.CAFE metadata, routes, and runtime signals | Start here; inspect `archive_manifest.json` before forcing Browser |
| Classic | Uses historical entry-page and static-asset behavior | Lowest cost, but it does not follow story routes or runtime-only assets |
| Smart | Adds a same-origin story-route crawl | Use when links change URLs but assets do not require rendering |
| Browser | Adds JavaScript rendering, response observation, scrolling, and optional safe interaction | Most expensive; use when runtime evidence requires it |
| Safe interaction | Scrolls incrementally and activates a narrow non-form allowlist | Mutation requests and risky controls remain blocked |
| Interaction Off | Renders and scrolls without activating controls | Use when the website state must not be changed |
| Maximum pages | Hard cap for saved same-origin routes | Increase only when the manifest reports the cap was reached |
| Maximum depth | Route hops from the entry page | `0` means entry only; 30 is a practical start for a large branching story |
| Runtime pages | Maximum routes rendered in the browser engine | Keep below the overall page cap because rendering is expensive |
| Settle time | Wait after load, scroll, or safe interaction | Increase for slow animations or delayed requests |
| Scroll steps | Maximum incremental lazy-load scrolls | High values are useful only for unusually long pages |
| Safe clicks | Maximum allowlisted activations per runtime page | `0` disables clicks without disabling rendering and scrolling |
| No-progress rounds | Consecutive rounds without useful discoveries before stopping | 2 is fast; 3–4 may help slowly staged sites |

Important relationships:

- Maximum pages caps the complete route crawl; Runtime pages caps only the
  subset opened by the browser.
- Maximum depth limits distance from the entry, while Maximum pages still caps
  total route count.
- Scroll, safe-click, settle, and no-progress limits apply only to Browser or
  an Auto decision that includes Browser.
- Safe interaction is not unrestricted automation: non-GET/HEAD requests,
  forms, and dangerous controls remain blocked.

## Suggested starting profiles

- One-page runtime gallery: Browser, 50 pages, depth 10.
- Medium branching story: Auto + Safe, 300 pages, depth 30, 12 runtime pages.
- Large story: Auto + Safe, 800 pages, depth 30, 20 runtime pages.
- Conventional linked pages: Smart, 300 pages, depth 30, interaction Off.

## Processing sequence

1. Classic saves the entry HTML and discoverable assets.
2. Auto inspects CYOA.CAFE metadata, project JSON, routes, and runtime signals.
3. Static scanning examines HTML, CSS, JavaScript, `srcset`, and known dynamic
   asset patterns.
4. Smart follows in-scope same-origin story links.
5. Routes are mapped to `routes/<slug>/index.html` and references are rewritten.
6. Browser capture scrolls incrementally and feeds observed assets back through
   the normal downloader pipeline.
7. Safe interaction activates allowlisted controls inside a read-mostly sandbox.
8. `archive_manifest.json` records the policy, Auto profile, route map,
   failures, limits, and runtime result.
9. Integrity verification examines actual dependencies instead of treating
   arbitrary JavaScript words such as `href` or `url` as references.

## Scope and limitations

- Crawling is restricted to HTTP(S), the same origin, and the initial story
  scope.
- Login, authentication, API, mail, telemetry, and external-domain links are
  not story routes.
- Queries that affect content remain part of cache keys and local names. Common
  cache-buster queries may be normalized.
- Next.js `/_next/image` requests are mapped back to the original image,
  including `srcset` and `imagesrcset` entries.
- The downloader does not bypass CAPTCHA, paywalls, age verification, or login.
- Safe interaction rejects login, send, report, vote, payment, delete, upload,
  and comment controls. Forms and non-GET/HEAD requests are not permitted.
- CYOA.CAFE records with `cyoa_pages` become direct offline galleries. Records
  with `iframe_url` resolve the real viewer instead of being misclassified as
  static images.
- A website that depends on a private backend cannot become fully offline by
  copying only its frontend.

## Inspect the output

Use **Serve** in the GUI or another local HTTP server. Important files include:

- `index.html`: archive entry point;
- `routes/.../index.html`: saved story routes;
- `archive_manifest.json`: decisions, mappings, progress, and failures;
- `backup_report.txt` and failure logs: unresolved dependencies.

Direct `file://` loading can fail because of modules, CORS, fetch, or service
worker restrictions. That failure does not by itself prove that archive files
are missing.

## Troubleshooting

- **Crawl stops exactly at the cap:** increase Maximum pages and create a new
  output.
- **An image appears only after interaction:** use Browser with Safe interaction;
  complex site-specific workflows may still need custom handling.
- **Entry page works but choices fail:** inspect the manifest and use Serve.
- **Many 404s resemble code fragments:** use the current integrity checker;
  older scanners could misread JavaScript expressions.
- **Cloudflare challenge:** configure Cloudflare/FlareSolverr in Settings and
  retry.

## Development rules

- Preserve Classic defaults and historical output contracts unless a migration
  is provided.
- Keep every universal stage bounded by origin, scope, page count, depth,
  timeout, and runtime page limits.
- Route runtime assets through the normal pipeline so retries, SSRF protection,
  caching, collision handling, and reports remain active.
- Do not rewrite dynamic fragments that JavaScript still needs to compose.
- Add a regression test for each framework or site pattern that causes a bug.
- Verify results through local HTTP and inspect both the console and local
  dependencies.
