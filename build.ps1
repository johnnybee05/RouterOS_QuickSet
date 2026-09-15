<#
.SYNOPSIS
    Builds dist\MikrotikQuickSetPlus.exe (single portable file, no Python needed).

.PARAMETER SkipTests
    Do not run the test suite before building.
#>
param(
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Invoke-Native {
    <# Runs a native program. Windows PowerShell 5.1 turns every stderr line into an error
       record when ErrorActionPreference is Stop (PyInstaller logs INFO to stderr), so success
       is decided by the exit code only. #>
    param([string]$File, [string[]]$Arguments)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        & $File @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        throw "'$File $($Arguments -join ' ')' failed with exit code $LASTEXITCODE"
    }
}

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "Creating virtual environment..."
    Invoke-Native "python" @("-m", "venv", ".venv")
    Invoke-Native $python @("-m", "pip", "install", "--upgrade", "pip")
    Invoke-Native $python @("-m", "pip", "install", "-e", ".[dev]")
}

if (-not $SkipTests) {
    Write-Host "Running tests..."
    Invoke-Native $python @("-m", "pytest", "-q")
}

Write-Host "Building executable..."
Invoke-Native $python @(
    "-m", "PyInstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--windowed",
    "--name", "MikrotikQuickSetPlus",
    "--icon", "assets\icon.ico",
    "--paths", "src",
    "--add-data", "src\mqs\model\resources;mqs\model\resources",
    "--add-data", "src\mqs\data;mqs\data",
    "--add-data", "assets\icon.ico;assets",
    "src\mqs\__main__.py"
)

$exe = Join-Path $root "dist\MikrotikQuickSetPlus.exe"
if (-not (Test-Path $exe)) { throw "Expected $exe was not produced" }
$sizeMb = [math]::Round((Get-Item $exe).Length / 1MB, 1)
Write-Host "Done: $exe ($sizeMb MB)"
