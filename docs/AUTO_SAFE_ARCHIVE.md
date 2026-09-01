# Auto (Safe) Archive

`Auto` is a coordinator, not a separate download engine. It selects the
lightest complete path from the available evidence:

1. A `cyoa.cafe/game/<id>` record with `cyoa_pages` uses direct PocketBase files
   and generates a catalog-independent offline gallery.
2. A record with `iframe_url` resolves the actual viewer and continues
   profiling there.
3. A valid project JSON uses the Classic project-first path and skips Browser.
4. Same-origin story routes use Smart, or Browser when runtime evidence is
   also strong.
5. An SPA, Next.js, Vite, or other runtime application without project data
   uses Browser.
6. Scannable HTML and assets use Classic.

## Safe interaction sandbox

- Candidates are limited to visible, non-form buttons, role-buttons, and
  summary controls.
- The allowlist includes Load More, Show More, Next, Continue, Expand, Reveal,
  and controls with `aria-expanded=false`.
- The denylist includes login, registration, submission, send, report,
  like/vote, payment, donation, deletion, sharing, upload, and comments.
- During interaction capture, non-GET/HEAD requests and document navigation are
  blocked. Popups and dialogs are closed.
- Clicks, scrolls, runtime pages, settle time, and no-progress rounds are always
  bounded.

## Manifest and auditability

`archive_manifest.json` records the requested and effective policy, Auto
profile, scroll count, productive interactions, blocked requests, route state,
and the reason Browser was skipped. This makes an Auto decision inspectable
without reconstructing it from logs.

## CYOA.CAFE records

A static record can generate:

- `index.html`;
- `images/pages/`;
- `images/previews/` when available;
- `images/cover/` when available;
- `cyoa_cafe_metadata.json` without large base64 payloads;
- `archive_manifest.json`.

Linked records still use the normal viewer resolver. This covers project-based
viewers, embedded data-URI images, and custom viewers that genuinely require a
browser fallback.

In project-first mode, redundant bundle scans before and after localization are
skipped because `process_images` already understands project structure. This
avoids duplicate work, random 404 attempts from minified JavaScript strings,
and browser sessions that add no asset coverage. Viewer HTML and CSS are still
processed recursively. Failed source assets remain absolute online
dependencies and are reported instead of becoming misleading local paths.
