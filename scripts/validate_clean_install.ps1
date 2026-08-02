<#
.SYNOPSIS
Validate a Mira Portfolio Windows bundle under hardened clean-profile isolation.

.EXAMPLE
.\scripts\validate_clean_install.ps1 -BundlePath .\dist\MiraPortfolio

.EXAMPLE
.\scripts\validate_clean_install.ps1 -BundlePath .\dist\MiraPortfolio -Build

.EXAMPLE
.\scripts\validate_clean_install.ps1 `
    -BundlePath .\dist\MiraPortfolio `
    -ReportPath .\artifacts\clean-install-validation-report.json
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$BundlePath,
    [switch]$Build,
    [switch]$PreserveDiagnostics,
    [string]$ReportPath
)

$ErrorActionPreference = "Stop"

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "Clean-install validation requires Windows."
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$buildScript = Join-Path $PSScriptRoot "build_windows.ps1"
$archivePath = Join-Path $projectRoot "artifacts\MiraPortfolio-0.1.0-internal-alpha-win64.zip"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The project virtual-environment interpreter is missing."
}

$interpreterDetails = & $python -c "import platform, sys; print(sys.executable); print(platform.architecture()[0]); print(sys.version_info[:2] == (3, 13))"
if ($LASTEXITCODE -ne 0 -or $interpreterDetails.Count -ne 3) {
    throw "The project Python interpreter could not be inspected."
}
if ([System.IO.Path]::GetFullPath($interpreterDetails[0]) -ne [System.IO.Path]::GetFullPath($python)) {
    throw "Clean-install orchestration requires the project virtual environment."
}
if ($interpreterDetails[1] -ne "64bit" -or $interpreterDetails[2] -ne "True") {
    throw "Clean-install orchestration requires 64-bit Python 3.13."
}

if ($Build) {
    & $buildScript
    if ($LASTEXITCODE -ne 0) {
        throw "The tracked Windows build workflow failed."
    }
}

$resolvedBundle = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $BundlePath))
if (-not (Test-Path -LiteralPath $resolvedBundle -PathType Container)) {
    throw "The requested Windows bundle directory is missing."
}

$validatorArguments = @(
    "-m",
    "scripts.clean_install_validation",
    "--bundle",
    $resolvedBundle,
    "--archive-path",
    $archivePath
)
if ($PreserveDiagnostics) {
    $validatorArguments += "--preserve-diagnostics"
}
if ($ReportPath) {
    $resolvedReport = [System.IO.Path]::GetFullPath((Join-Path (Get-Location) $ReportPath))
    $validatorArguments += @("--report-path", $resolvedReport)
}

Push-Location $projectRoot
try {
    & $python @validatorArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Clean-install validation failed."
    }
}
finally {
    Pop-Location
}
