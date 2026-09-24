param(
    [switch]$SkipInstall,
    [switch]$SkipChecks
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = (Get-Command python -ErrorAction Stop).Source
$Version = (Get-Content -LiteralPath (Join-Path $Root "VERSION") -Raw).Trim()
Write-Host "Building CYOA Downloader v$Version"

function Invoke-Python {
    param(
        [Parameter(ValueFromRemainingArguments = $true)]
        [string[]]$PythonArguments
    )
    & $Python @PythonArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE`: python $($PythonArguments -join ' ')"
    }
}

if (-not $SkipInstall) {
    Write-Host "[1/4] Installing Python dependencies..."
    Invoke-Python -m pip install -r requirements.txt
    Invoke-Python -m pip install -r requirements-optional.txt
    Invoke-Python -m pip install -r requirements-dev.txt
}

if (-not $SkipChecks) {
    Write-Host "[2/4] Running release checks..."
    Invoke-Python -m compileall -q cyoa_downloader_app cyoa_downloader.py
    Invoke-Python -m pytest -q
    Invoke-Python -m ruff check .
    Invoke-Python cyoa_downloader.py --self-test
}

$LegacyBundle = Join-Path $Root "dist\CYOA Downloader"
if (Test-Path -LiteralPath $LegacyBundle) {
    $LegacyItem = Get-Item -LiteralPath $LegacyBundle -Force
    if (($LegacyItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a reparse-point build output: $LegacyBundle"
    }
    $ResolvedLegacyBundle = (Resolve-Path -LiteralPath $LegacyBundle).Path
    $ResolvedDist = [System.IO.Path]::GetFullPath((Join-Path $Root "dist"))
    if (-not $ResolvedLegacyBundle.StartsWith($ResolvedDist + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a bundle outside the build output directory: $ResolvedLegacyBundle"
    }
    [System.IO.Directory]::Delete($ResolvedLegacyBundle, $true)
}

Write-Host "[3/4] Creating the Windows executable with the black icon..."
Invoke-Python -m PyInstaller --clean --noconfirm (Join-Path $Root "CYOA-Downloader.spec")

$Executable = Join-Path $Root "dist\CYOA Downloader.exe"
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller output not found: $Executable"
}

$Archive = Join-Path $Root "dist\CYOA-Downloader-Windows-x64.zip"
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
Compress-Archive -Path $Executable -DestinationPath $Archive
Write-Host "[4/4] Created $Archive"
