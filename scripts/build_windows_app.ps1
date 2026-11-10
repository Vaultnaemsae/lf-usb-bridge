param(
    [string]$PythonExecutable = 'python',
    [string]$BuildLabel = 'LF12-test-20261110',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
if (!$OutputDirectory) { $OutputDirectory = Join-Path $root "artifacts\windows-$BuildLabel" }
if ($BuildLabel -notmatch '^[A-Za-z0-9._-]+$') { throw 'Invalid build label' }
if (Test-Path -LiteralPath $OutputDirectory) { throw "Output already exists: $OutputDirectory" }
New-Item -ItemType Directory -Path $OutputDirectory | Out-Null
$OutputDirectory = (Resolve-Path -LiteralPath $OutputDirectory).Path
Push-Location $root
try {
    & $PythonExecutable -c "import struct, sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8"
    if ($LASTEXITCODE) { throw 'Windows 64-bit Python is required' }
    $env:QT_QPA_PLATFORM = 'offscreen'
    $env:PYTHONDONTWRITEBYTECODE = '1'
    & $PythonExecutable -m pytest -q -p no:cacheprovider 2>&1 |
        Tee-Object -FilePath (Join-Path $OutputDirectory 'tests.log')
    if ($LASTEXITCODE) { throw 'Tests failed' }
    & $PythonExecutable -m pip freeze | Set-Content (Join-Path $OutputDirectory 'dependencies.txt')
    & $PythonExecutable -m PyInstaller --noconfirm --clean `
        --workpath (Join-Path $OutputDirectory 'work') `
        --distpath (Join-Path $OutputDirectory 'dist') `
        (Join-Path $PSScriptRoot 'lf_usb_bridge_windows.spec')
    if ($LASTEXITCODE) { throw 'Windows packaging failed' }
    $bundle = Join-Path $OutputDirectory 'dist\LF+ USB Bridge'
    Copy-Item LICENSE, THIRD_PARTY_NOTICES.md -Destination $bundle
    Copy-Item (Join-Path $PSScriptRoot 'WINDOWS_LF12_TEST_README.txt') `
        -Destination (Join-Path $bundle 'READ ME FIRST.txt')
    if (Test-Path 'SOURCE_MANIFEST.json') { Copy-Item SOURCE_MANIFEST.json -Destination $bundle }
    & $PythonExecutable (Join-Path $PSScriptRoot 'verify_windows_bundle.py') $bundle |
        Tee-Object -FilePath (Join-Path $OutputDirectory 'verification.json')
    if ($LASTEXITCODE) { throw 'Packaged application verification failed' }
    $zip = Join-Path $OutputDirectory "LF+ USB Bridge-Windows-x64-$BuildLabel.zip"
    Compress-Archive -LiteralPath $bundle -DestinationPath $zip -CompressionLevel Optimal
    Get-FileHash -Algorithm SHA256 -LiteralPath $zip | Format-List
    Write-Output "Test package: $zip"
} finally {
    Pop-Location
}
