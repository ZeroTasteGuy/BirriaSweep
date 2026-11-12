# Builds the BirriaSweep Windows package: dist\BirriaSweep-windows-x64.zip
#
# Needs: git, Visual Studio 2022 C++ Build Tools (with CMake), Python 3 with pip, Node.js 22.
# Run from the repository root:  powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
[CmdletBinding()]
param(
    [string]$BirriaRepo = 'https://github.com/ot0tot/BirriaSDR',
    [string]$BirriaRef = '35439a93fd845c1105559382b17f35c1a0abb5b9',   # the patch is made against this commit
    [string]$PageRepo = 'https://github.com/ZeroTasteGuy/BrowSDR_Sweep',
    [string]$PageRef = 'main',
    [string]$PythonVersion = '3.12.10',
    [string]$Work = 'build',
    [switch]$SkipTests
)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force $Work, dist | Out-Null
$Work = (Resolve-Path $Work).Path
function Run { & $args[0] $args[1..($args.Count - 1)]; if ($LASTEXITCODE) { throw "failed: $args" } }

# 1. Driver: BirriaSDR at the pinned commit plus the fast-sweep patch.
$birria = Join-Path $Work 'BirriaSDR'
if (!(Test-Path $birria)) { Run git clone --quiet $BirriaRepo $birria }
Push-Location $birria
Run git fetch --quiet origin $BirriaRef
Run git checkout --quiet --force $BirriaRef
Run git clean -fdq -e build-windows
Run git apply (Join-Path $root 'driver\birriasdr-fast-sweep.patch')
$buildArgs = @{}; if ($SkipTests) { $buildArgs.SkipTests = $true }
& .\tools\build-windows.ps1 @buildArgs
Pop-Location

# 2. Page: the BrowSDR build, served by the bridge from the package root.
$page = Join-Path $Work 'BrowSDR'
if (!(Test-Path $page)) { Run git clone --quiet $PageRepo $page }
Push-Location $page
Run git fetch --quiet origin $PageRef
Run git checkout --quiet --force FETCH_HEAD
Run npm.cmd ci --no-audit --no-fund
$env:BROWSDR_BASE = '/'
Run npx.cmd vite build
Pop-Location

# 3. Python: the official embeddable build plus numpy, so nothing has to be installed.
$py = Join-Path $Work 'python'
if (!(Test-Path (Join-Path $py 'python.exe'))) {
    $zip = Join-Path $Work 'python-embed.zip'
    Invoke-WebRequest "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip" -OutFile $zip
    Expand-Archive $zip $py -Force
    $tag = ($PythonVersion -split '\.')[0..1] -join ''
    $wheels = Join-Path $Work 'wheels'
    Run python -m pip download --quiet numpy --only-binary=:all: --platform win_amd64 --python-version $tag `
        --implementation cp -d $wheels
    Get-ChildItem $wheels -Filter *.whl | ForEach-Object {
        $z = "$($_.FullName).zip"; Copy-Item $_.FullName $z; Expand-Archive $z $py -Force
    }
}

# 4. Assemble and zip.
$pkg = Join-Path $Work 'BirriaSweep'
if (Test-Path $pkg) { Remove-Item -Recurse -Force $pkg }
New-Item -ItemType Directory -Force $pkg, "$pkg\bridge", "$pkg\lib", "$pkg\docs" | Out-Null
Copy-Item start\*.bat $pkg
Copy-Item bridge\birria_bridge.py "$pkg\bridge"
Copy-Item "$birria\build-windows\Release\*.dll" "$pkg\lib"
Copy-Item -Recurse "$page\dist" "$pkg\ui"
Copy-Item -Recurse $py "$pkg\python"
Copy-Item docs\*.md "$pkg\docs"
Copy-Item README.md, LICENSE $pkg
Copy-Item "$birria\LICENSE" "$pkg\lib\BirriaSDR-LICENSE.txt"
Copy-Item "$page\LICENSE" "$pkg\ui\BrowSDR-LICENSE.txt"
foreach ($n in 'birriasdr.dll', 'birria_display.dll', 'libusb-1.0.dll') {
    if (!(Test-Path "$pkg\lib\$n")) { throw "missing $n in the driver build" }
}
$out = Join-Path $root 'dist\BirriaSweep-windows-x64.zip'
if (Test-Path $out) { Remove-Item $out }
Compress-Archive $pkg $out
Write-Host "Package: $out"
