# fetch_vendor.ps1 — extract xterm.js UMD builds from npm tarballs
# Run once before Phase 2b development; commit vendor files afterward.
#
# Output:
#   vox/ui_web/vendor/xterm.js
#   vox/ui_web/vendor/xterm.css
#   vox/ui_web/vendor/xterm-addon-fit.js

$ErrorActionPreference = 'Stop'
$root   = Split-Path $PSScriptRoot -Parent
$vendor = Join-Path $root 'vox\ui_web\vendor'
$tmp    = Join-Path $env:TEMP "vox-vendor-$([System.Guid]::NewGuid().ToString('N').Substring(0,8))"

New-Item -ItemType Directory -Force -Path $vendor | Out-Null
New-Item -ItemType Directory -Force -Path $tmp    | Out-Null

function Unpack-Package {
    param([string]$Package, [string]$Version, [hashtable]$Files)

    $tarFile = "$($Package -replace '/','-')-$Version.tgz"
    $tarPath = Join-Path $tmp $tarFile

    Write-Host "Packing $Package@$Version..."
    $prev = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    cmd /c "npm pack $Package@$Version --pack-destination=`"$tmp`" 2>nul"
    $ErrorActionPreference = $prev

    if (-not (Test-Path $tarPath)) {
        throw "Expected tarball not found: $tarPath"
    }

    tar -xf $tarPath -C $tmp
    foreach ($pair in $Files.GetEnumerator()) {
        $src  = Join-Path $tmp "package\$($pair.Key)"
        $dest = Join-Path $vendor $pair.Value
        Copy-Item $src $dest -Force
        Write-Host "  -> $($pair.Value)"
    }
    Remove-Item -Recurse -Force (Join-Path $tmp 'package'), $tarPath
}

try {
    Unpack-Package 'xterm' '5.3.0' @{
        'lib\xterm.js'  = 'xterm.js'
        'css\xterm.css' = 'xterm.css'
    }
    Unpack-Package 'xterm-addon-fit' '0.8.0' @{
        'lib\xterm-addon-fit.js' = 'xterm-addon-fit.js'
    }
} finally {
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
}

Write-Host "`nDone. Vendor files:"
Get-ChildItem $vendor | ForEach-Object {
    Write-Host "  $($_.Name)  ($([math]::Round($_.Length / 1KB, 1)) KB)"
}
