param(
    [switch]$SkipInstall,
    [switch]$SkipChecks,
    [ValidateSet("onefile", "onedir")]
    [string]$Layout = "onefile"
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Python = (Get-Command python -ErrorAction Stop).Source
$RepoRoot = Split-Path -Parent $Root
$Version = (Get-Content -LiteralPath (Join-Path $RepoRoot "VERSION") -Raw).Trim()
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
if ($Layout -eq "onefile" -and (Test-Path -LiteralPath $LegacyBundle)) {
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
$PreviousBuildLayout = $env:CYOA_BUILD_LAYOUT
try {
    $env:CYOA_BUILD_LAYOUT = $Layout
    if ($Layout -eq "onedir") {
        # PyInstaller replaces its generated bundle on repeated builds. Reject
        # redirected output directories before allowing that recursive cleanup.
        foreach ($OutputPath in @(
            (Join-Path $Root "dist"),
            (Join-Path $Root "dist\portable"),
            (Join-Path $Root "dist\portable\CYOA Downloader")
        )) {
            if (Test-Path -LiteralPath $OutputPath) {
                $OutputItem = Get-Item -LiteralPath $OutputPath -Force
                if (($OutputItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                    throw "Refusing a redirected build output: $OutputPath"
                }
            }
        }
        Invoke-Python -m PyInstaller --noconfirm --distpath (Join-Path $Root "dist\portable") --workpath (Join-Path $Root "build\portable") (Join-Path $Root "CYOA-Downloader.spec")
    } else {
        Invoke-Python -m PyInstaller --clean --noconfirm (Join-Path $Root "CYOA-Downloader.spec")
    }
} finally {
    $env:CYOA_BUILD_LAYOUT = $PreviousBuildLayout
}

$Executable = if ($Layout -eq "onedir") {
    Join-Path $Root "dist\portable\CYOA Downloader\CYOA Downloader.exe"
} else {
    Join-Path $Root "dist\CYOA Downloader.exe"
}
if (-not (Test-Path -LiteralPath $Executable)) {
    throw "PyInstaller output not found: $Executable"
}

$Archive = if ($Layout -eq "onedir") {
    Join-Path $Root "dist\portable\CYOA-Downloader-Windows-x64-portable.zip"
} else {
    Join-Path $Root "dist\CYOA-Downloader-Windows-x64.zip"
}
if (Test-Path -LiteralPath $Archive) {
    Remove-Item -LiteralPath $Archive -Force
}
$ArchiveSource = if ($Layout -eq "onedir") { Split-Path -Parent $Executable } else { $Executable }
Compress-Archive -Path $ArchiveSource -DestinationPath $Archive
Write-Host "[4/4] Created $Archive"
