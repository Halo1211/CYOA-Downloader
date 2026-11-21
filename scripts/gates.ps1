$ErrorActionPreference = "Stop"

function Invoke-Gate {
    param(
        [string]$Name,
        [string[]]$CargoArgs
    )

    Write-Host "Running gate: $Name"
    & cargo @CargoArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Gate failed: $Name (exit code $LASTEXITCODE)"
        exit $LASTEXITCODE
    }
}

Invoke-Gate "cargo fmt --all -- --check" @("fmt", "--all", "--", "--check")
Invoke-Gate "cargo check --all-targets" @("check", "--all-targets")
Invoke-Gate "cargo clippy --all-targets -- -D warnings" @("clippy", "--all-targets", "--", "-D", "warnings")
Invoke-Gate "cargo test" @("test")
