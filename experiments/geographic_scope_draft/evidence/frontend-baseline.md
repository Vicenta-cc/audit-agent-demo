# Frontend baseline evidence (before edits)

Captured from tool command results on 2026-10-15; original baseline outputs were not redirected to files. These are result summaries, not reconstructed full logs.

- HEAD a4997d3969beab3d499a218e7a7aee025fea46d9; frontend initially clean.
- Dependencies: symlink node_modules to existing baseline worktree dependencies, no install.
- `npm run typecheck`: exit 0.
- `npm run build`: failed because VITE_API_PROXY_TARGET was absent (required by existing vite.config.ts).
- `VITE_API_PROXY_TARGET=http://127.0.0.1:8010 npm run build`: exit 0, 2000 modules, existing >500 kB chunk warning.
- `npm run test -- --config=playwright.config.ts tests/investigationCreation.spec.tsx tests/taskConfirmationCardLimits.spec.ts tests/taskSuggestionKeywordEditing.spec.ts`: 46 passed, 8 launch failures because bundled Playwright Chromium was unavailable.
- `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' npm run test -- --config=playwright.config.ts tests/investigationCreation.spec.tsx tests/taskConfirmationCardLimits.spec.ts tests/taskSuggestionKeywordEditing.spec.ts`: 54 passed (7.8s).

Screenshots in this directory use synthetic local public projections and mounted real React components. They prove display behavior only, not model interpretation or real backend persistence. Backend/service evidence is supplied separately by the integration owner.
