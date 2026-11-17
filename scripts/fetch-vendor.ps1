# Fetches the vendored frontend dependencies from the npm registry, pinned and
# verified against their SHA-256 hashes. These files are intentionally NOT
# committed to git (they are ~300KB of minified third-party code).
#
# Usage:  powershell -ExecutionPolicy Bypass -File scripts\fetch-vendor.ps1
$ErrorActionPreference = 'Stop'

$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$vendor = Join-Path $src '..\frontend\assets\vendor'
New-Item -ItemType Directory -Force -Path $vendor | Out-Null

$files = @(
    @{ name = 'xterm.js';               url = 'https://unpkg.com/xterm@5.3.0/lib/xterm.js';                                   sha = 'F0AEA0F75F48559013AE6643C2479DD737D26DA42D5524E6D2B70915AE6523C7' },
    @{ name = 'xterm.css';              url = 'https://unpkg.com/xterm@5.3.0/css/xterm.css';                                  sha = '832F3F2C603B43AD4351FF04970150CC7A873014276DB126A6065C6DD81E4872' },
    @{ name = 'xterm-addon-fit.js';     url = 'https://unpkg.com/xterm-addon-fit@0.8.0/lib/xterm-addon-fit.js';                sha = '10F3194C5F17C1786FB7D5DB865C1EC8539B6736A318063FD38BDAAF7C46848F' },
    @{ name = 'xterm-addon-search.js';  url = 'https://unpkg.com/xterm-addon-search@0.13.0/lib/xterm-addon-search.js';         sha = '6A6DB33F16B764552377A2C5BA4327C6DAB6BEAF25484533AFD7DBECD0B03793' },
    @{ name = 'xterm-addon-web-links.js'; url = 'https://unpkg.com/xterm-addon-web-links@0.9.0/lib/xterm-addon-web-links.js';   sha = 'D67624AB0E89E1D2EE27B64CC6E9283F4CE206D7343429D87D95370B0C971824' }
)

foreach ($f in $files) {
    $dest = Join-Path $vendor $f.name
    Write-Host "fetching $($f.name)…"
    Invoke-WebRequest -Uri $f.url -OutFile $dest -UseBasicParsing
    $actual = (Get-FileHash -Algorithm SHA256 $dest).Hash
    if ($actual -ne $f.sha) {
        Remove-Item $dest -Force
        throw "SHA-256 mismatch for $($f.name)`n  expected: $($f.sha)`n  got:      $actual`nAborting. The vendored file was removed; the app cannot start without it."
    }
    Write-Host "  ok ($($f.sha.Substring(0,12))…)"
}
Write-Host "All vendored frontend dependencies present and verified in $vendor"
