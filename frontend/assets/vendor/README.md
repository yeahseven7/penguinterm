# Vendored frontend dependencies

These files are **not committed to git** — they are ~300KB of minified
third-party code (xterm.js and three addons), fetched on demand from the npm
registry and verified against pinned SHA-256 hashes.

To populate this directory:

```powershell
poweshell -ExecutionPolicy Bypas -File script\fetch-vendor.ps1
```

```bash
./scripts/fetch-vendor.sh
```

Both scripts download:

| File | Package | Version |
|------|---------|---------|
| xterm.js, xterm.css | `xterm` | 5.3.0 |
| xterm-addon-fit.js | `xterm-addon-fit` | 0.8.0 |
| xterm-addon-search.js | `xterm-addon-search` | 0.13.0 |
| xterm-addon-web-links.js | `xterm-addon-web-links` | 0.9.0 |

Each download's SHA-256 is checked against the hashes
hard-coded in the
fetch script; on mismatch the file is deleted and the script aborts.

xterm.js is MIT-licensed, (c) 2023 xterm.js contributors — see LICENSE.
