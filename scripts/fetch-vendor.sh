#!/usr/bin/env bash
# Fetches the vendored frontend dependencies from the npm registry, pinned and
# verified against their SHA-256 hashes. These files are intentionally NOT
# committed to git (they are ~300KB of minified third-party code).
#
# Usage:  ./scripts/fetch-vendor.sh
set -euo pipefail

SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENDOR_DIR="$SRC_DIR/../frontend/assets/vendor"
mkdir -p "$VENDOR_DIR"

FILES=(
  "xterm.js|https://unpkg.com/xterm@5.3.0/lib/xterm.js|F0AEA0F75F48559013AE6643C2479DD737D26DA42D5524E6D2B70915AE6523C7"
  "xterm.css|https://unpkg.com/xterm@5.3.0/css/xterm.css|832F3F2C603B43AD4351FF04970150CC7A873014276DB126A6065C6DD81E4872"
  "xterm-addon-fit.js|https://unpkg.com/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js|10F3194C5F17C1786FB7D5DB865C1EC8539B6736A318063FD38BDAAF7C46848F"
  "xterm-addon-search.js|https://unpkg.com/xterm-addon-search@0.13.0/lib/xterm-addon-search.js|6A6DB33F16B764552377A2C5BA4327C6DAB6BEAF25484533AFD7DBECD0B03793"
  "xterm-addon-web-links.js|https://unpkg.com/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.js|D67624AB0E89E1D2EE27B64CC6E9283F4CE206D7343429D87D95370B0C971824"
)

for entry in "${FILES[@]}"; do
  name="${entry%%|*}"; rest="${entry#*|}"
  url="${rest%%|*}"; sha="${rest##*|}"
  dest="$VENDOR_DIR/$name"
  echo "fetching $name…"
  curl -fsSL --retry 2 -o "$dest" "$url"
  actual="$(sha256sum "$dest" | awk '{print toupper($1)}')"
  if [ "$actual" != "$sha" ]; then
    rm -f "$dest"
    echo "SHA-256 mismatch for $name" >&2
    echo "  expected: $sha" >&2
    echo "  got:      $actual" >&2
    exit 1
  fi
  echo "  ok (${sha:0:12}…)"
done
echo "All vendored frontend dependencies present and verified in $VENDOR_DIR"
