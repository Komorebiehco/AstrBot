# Native Plugin Page build

Run `npm ci --no-audit --no-fund`, then `npm run build` from this directory.
Commit the lockfile and both generated files in `../pages/proactive-chat/js/`:
`vendor.bundle.js` and `app.bundle.js`. Do not deploy `node_modules`.

The build uses pinned dependencies, selects only the MUI exports used by the
views, and precompiles JSX. The native page loads two same-origin scripts and
preserves AstrBot's asset-token and API bridge authentication. Mermaid loads
only when a document contains a diagram; an unavailable CDN leaves readable
diagram source without blocking the tasks page.

The independent `admin/` entry remains compatible with its existing loader.
Runtime configuration and session data are not part of this build.
