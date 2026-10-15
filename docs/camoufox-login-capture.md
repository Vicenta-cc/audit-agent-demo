# Camoufox login capture contract

The account browser uses a persistent Firefox Profile, a durable final fingerprint,
and its bound route. macOS runs headless so login does not open a separate window;
Linux runs headed in one private Xvfb. The system receives the QR or challenge
image and forwards user interaction. A preparation error never reopens another
browser mode. After a confirmed login, the Profile is flushed and reopened once
to verify persistence before committing credentials. This verification retains
the same fingerprint, route, and authority channel and requires confirmed process
teardown before reopening.

## Screenshot capability limit

`CAPTURE_CAPABILITIES` in `scripts/crawler_douyin_login_v2.py` explicitly declares
`native_caret: false`. Firefox's compositor snapshot does not paint the native
text insertion caret. Passing Playwright `caret="initial"` preserves native
behavior but cannot make a caret appear in that snapshot. This differs from the
old Chromium capture behavior and is a release review limitation, not evidence
that the browser input is unfocused.

The capture continues to include the real input focus ring and editable-target
metadata with `focused`, `selection_start`, and `selection_end`. The real-browser
regression verifies the missing caret, actual focus, selection positions and
keyboard editing. No fake caret is injected into the platform page. The client projects the native editor caret separately over the existing frame,
using Firefox `CaretPosition.getClientRect()` for an exact native hit matching
the original input's collapsed selection. Only normalized geometry and computed
caret color are added to editable metadata, not additional input text. The
projection reuses confirmed focus, applied input revision/sequence and frame
area/epoch gates, and disappears on pending edits, blur or lost control. There
is no extra visible input or field outline. Unsupported transforms, noncollapsed
selection and unavailable native geometry hide the projected caret. This does
not change `native_caret=False` or claim the screenshot now paints it.

Sources:

- [Camoufox beta.36 PageHandler screenshot implementation](https://github.com/daijro/camoufox/blob/v156.0.1-beta.36/additions/juggler/protocol/PageHandler.js#L357)
- [Playwright caret screenshot option](https://playwright.dev/python/docs/api/class-page#page-screenshot-option-caret)

## Navigation and credential reset

The mandatory local Firefox extension registers a blocking `webRequest` listener
before its readiness ACK. It restricts document destinations before transmission,
including redirects and popup initial requests. Context routes additionally
filter only explicitly unwanted video media and preserve login scripts, QR images,
XHR and challenge resources. Ordinary resources do not consume a shared 32-slot
request limit. The exact probe URL retains separate no-redirect and no-credential
transport rules.

Explicit reauthentication closes old renderers and clears platform cookies,
local/session storage, IndexedDB, ServiceWorker registrations and CacheStorage
through Firefox public APIs. A storage reset failure blocks the operation. It
does not load Chromium auth snapshots into the new Profile.

Reproducible checks are in `tests/test_crawler_douyin_login_browser.py`,
`tests/test_camoufox_native_navigation_guard.py`,
`tests/test_firefox_login_navigation_policy.py`, and
`tests/test_login_guard_composition.py`. Optional actual-browser fixtures require
`RUN_CAMOUFOX_LOGIN_TESTS=1`, a preinstalled pinned browser/model, and
`MEDIACRAWLER_CAMOUFOX_BROWSER_VERSION` matching that artifact. The composition
fixture additionally uses `LOGIN_BROWSER_TEST_PYTHON` and a test-only NSS CA.

## Bounded login preparation

`CRAWLER_LOGIN_V2_PREPARATION_TIMEOUT_SECONDS` defaults to 120 seconds and is
bounded to 15–180 seconds. The owner publishes the configured value through the
restricted child environment. DOM readiness and login-control hydration share
one deadline inside `open_login`; a ready QR/panel returns immediately. Expiry
raises `LOGIN_PREPARATION_FAILED` and does not restart the browser. The overall
login session and per-action deadlines are unchanged.

## First-party credential backup

The verified persistent Profile remains the source of truth. Database auth
backup uses `export_douyin_auth_state`, which filters cookies to `douyin.com`
and its subdomains and reads localStorage only from already loaded first-party
frames. Frame-origin changes or conflicting origin state abort the export.
The exporter creates no pages and performs no network navigation. It never
exports verification iframe or exit-probe credentials.

Explicit reauthentication also avoids `context.storage_state()`: origin names
come from cookies, existing documents and Firefox's first-party storage directory
names. Playwright's all-origin collector can promote a verification iframe origin
to a main-frame navigation, which the navigation policy correctly blocks. Neither
backup nor reset weakens that policy to accommodate the collector.

Caret hit testing verifies the real collapsed offset. Pure RTL has fixture
coverage; mixed bidi strong/weak visual affinity is not claimed to match every
native painted caret position. The projection does not reconstruct or export
field text to solve that separate native-affinity ambiguity.
