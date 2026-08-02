<#
.SYNOPSIS
Build the Mira Portfolio Windows one-folder bundle with the project virtual environment.

.EXAMPLE
.\scripts\build_windows.ps1

.EXAMPLE
.\scripts\build_windows.ps1 -Verify
#>
[CmdletBinding()]
param(
    [switch]$Verify,
    [switch]$KeepVerificationArtifacts
)

$ErrorActionPreference = "Stop"

function Remove-OwnedBuildDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$AllowedParent,
        [Parameter(Mandatory = $true)][string]$ExpectedLeaf
    )

    $resolvedPath = [System.IO.Path]::GetFullPath($Path)
    $resolvedParent = [System.IO.Path]::GetFullPath($AllowedParent)
    if (
        [System.IO.Path]::GetDirectoryName($resolvedPath) -ne $resolvedParent -or
        [System.IO.Path]::GetFileName($resolvedPath) -ne $ExpectedLeaf
    ) {
        throw "Refusing to clean a path outside the owned build output."
    }

    if (Test-Path -LiteralPath $resolvedPath) {
        $item = Get-Item -LiteralPath $resolvedPath -Force
        if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Refusing to clean a linked build output."
        }
        Remove-Item -LiteralPath $resolvedPath -Recurse -Force
    }
}

if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "The Windows bundle must be built on Windows."
}

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$spec = Join-Path $projectRoot "packaging\windows\MiraPortfolio.spec"
$buildParent = Join-Path $projectRoot "build"
$distParent = Join-Path $projectRoot "dist"
$buildOutput = Join-Path $buildParent "MiraPortfolio"
$bundleOutput = Join-Path $distParent "MiraPortfolio"
$executable = Join-Path $bundleOutput "MiraPortfolio.exe"

if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    throw "The project virtual-environment interpreter is missing."
}
if (-not (Test-Path -LiteralPath $spec -PathType Leaf)) {
    throw "The tracked Windows spec file is missing."
}

$interpreterDetails = & $python -c "import platform, sys; print(sys.executable); print(platform.architecture()[0]); print(sys.version_info[:2] == (3, 13))"
if ($LASTEXITCODE -ne 0 -or $interpreterDetails.Count -ne 3) {
    throw "The project Python interpreter could not be inspected."
}
if ([System.IO.Path]::GetFullPath($interpreterDetails[0]) -ne [System.IO.Path]::GetFullPath($python)) {
    throw "The build interpreter is not the project virtual environment."
}
if ($interpreterDetails[1] -ne "64bit") {
    throw "The Windows bundle requires 64-bit Python."
}
if ($interpreterDetails[2] -ne "True") {
    throw "The Windows bundle requires Python 3.13."
}

$pyInstallerVersion = (& $python -m PyInstaller --version).Trim()
if ($LASTEXITCODE -ne 0 -or $pyInstallerVersion -ne "6.21.0") {
    throw "PyInstaller 6.21.0 must be installed in the project virtual environment."
}

Remove-OwnedBuildDirectory -Path $buildOutput -AllowedParent $buildParent -ExpectedLeaf "MiraPortfolio"
Remove-OwnedBuildDirectory -Path $bundleOutput -AllowedParent $distParent -ExpectedLeaf "MiraPortfolio"

Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean --workpath $buildParent --distpath $distParent $spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller failed to create the Windows bundle."
    }
}
finally {
    Pop-Location
}

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "PyInstaller completed without producing MiraPortfolio.exe."
}

$bundleBytes = (Get-ChildItem -LiteralPath $bundleOutput -Recurse -File | Measure-Object -Property Length -Sum).Sum
Write-Output "Build directory: $buildOutput"
Write-Output "Distribution directory: $bundleOutput"
Write-Output "Bundle size (bytes): $bundleBytes"

if ($Verify) {
    $verifier = Join-Path $projectRoot "scripts\verify_windows_bundle.py"
    $arguments = @($verifier, $bundleOutput)
    if ($KeepVerificationArtifacts) {
        $arguments += "--keep-on-failure"
    }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Windows bundle verification failed."
    }
}
