$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

python -m pip install -r requirements.txt
python -m pip install -r requirements-optional.txt
python -m pip install -r requirements-dev.txt

$LegacyBundle = Join-Path $Root "dist\CYOA Downloader"
if (Test-Path -LiteralPath $LegacyBundle) {
    [System.IO.Directory]::Delete((Resolve-Path -LiteralPath $LegacyBundle).Path, $true)
}

python -m PyInstaller --clean --noconfirm (Join-Path $Root "CYOA-Downloader.spec")

$Executable = Join-Path $Root "dist\CYOA Downloader.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller output not found: $Executable"
}

$Archive = Join-Path $Root "dist\CYOA-Downloader-Windows-x64.zip"
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -Path $Executable -DestinationPath $Archive
Write-Host "Created $Archive"
