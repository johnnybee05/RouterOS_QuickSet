<#
.SYNOPSIS
    Vydá verzi popsanou v CHANGELOG.md: srovná verzi ve zdrojích, postaví
    dist\MikrotikQuickSetPlus.exe, otaguje, pushne a založí vydání na GitHubu
    s textem vygenerovaným z changelogu.

.DESCRIPTION
    Changelog je zdroj. Napište do něj oddíl "## [X.Y.Z] - <datum>", commitněte ho,
    a pak tímto skriptem vydejte -- text vydání se nepíše podruhé.

.PARAMETER Version
    Verze bez "v", třeba 0.3.0. Musí být v CHANGELOG.md.

.PARAMETER DryRun
    Nic necommitne, nepushne ani nepublikuje: jen ověří stav a vypíše text vydání.

.PARAMETER Resync
    Vydání už existuje a má se mu jen přepsat text podle changelogu.

.PARAMETER SkipBuild
    Nestaví exe znovu; použije se to, co je v dist\. Testy tím taky neproběhnou.

.EXAMPLE
    .\release.ps1 -Version 0.3.0 -DryRun
    .\release.ps1 -Version 0.3.0
#>
param(
    [Parameter(Mandatory = $true)][string]$Version,
    [switch]$DryRun,
    [switch]$Resync,
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Invoke-Native {
    <# Stejně jako v build.ps1: o úspěchu rozhoduje jen exit code, protože
       Windows PowerShell 5.1 dělá z každého řádku na stderr chybový záznam. #>
    param([string]$File, [string[]]$Arguments, [switch]$Quiet)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $output = $null
    try {
        if ($Quiet) {
            $output = & $File @Arguments 2>&1 | ForEach-Object { "$_" }
        } else {
            & $File @Arguments 2>&1 | ForEach-Object { "$_" } | Out-Host
        }
    } finally {
        $ErrorActionPreference = $previous
    }
    if ($LASTEXITCODE -ne 0) {
        if ($null -ne $output) { $output | Out-Host }
        throw "'$File $($Arguments -join ' ')' skončilo s exit code $LASTEXITCODE"
    }
    return $output
}

function Set-FileVersion {
    param([string]$Path, [string]$Pattern, [string]$Replacement)
    $text = Get-Content -Raw -Encoding UTF8 $Path
    $updated = [regex]::Replace($text, $Pattern, $Replacement)
    if ($updated -ne $text) {
        Set-Content -Path $Path -Value $updated -Encoding UTF8 -NoNewline
        Write-Host "  $Path -> $Version"
        return $true
    }
    return $false
}

if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Verze '$Version' nemá tvar X.Y.Z"
}
$tag = "v$Version"

$python = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "Chybí .venv -- spusťte nejdřív .\build.ps1" }
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) { throw "Chybí gh (GitHub CLI)" }

# 1. Changelog musí verzi mít, než se cokoli staví.
Write-Host "Changelog..."
Invoke-Native $python @("-m", "tools.release_notes", "--version", $Version, "--check")
Write-Host "  CHANGELOG.md má oddíl pro $Version"

# 2. Strom musí být čistý, aby se do vydání nedostalo nic nezapsaného.
$dirty = Invoke-Native "git" @("status", "--porcelain") -Quiet
if ($dirty) {
    $dirty | Out-Host
    throw "Pracovní strom není čistý -- commitněte, nebo zahoďte změny"
}

# 3. Existující vydání se přepisuje jen na vyžádání.
$released = $null
$previous = $ErrorActionPreference
$ErrorActionPreference = "Continue"
& gh release view $tag --json tagName 2>&1 | Out-Null
$released = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = $previous
if ($released -and -not $Resync) {
    throw "Vydání $tag už existuje -- text z changelogu mu přepíšete pomocí -Resync"
}
if ($Resync -and -not $released) { throw "Vydání $tag neexistuje, není co přepsat" }

# 4. Verze ve zdrojích a v golden skriptech.
if (-not $Resync) {
    Write-Host "Verze ve zdrojích..."
    $bumped = $false
    $bumped = (Set-FileVersion "pyproject.toml" '(?m)^version = "\d+\.\d+\.\d+"$' "version = `"$Version`"") -or $bumped
    $bumped = (Set-FileVersion "src\mqs\__init__.py" '(?m)^__version__ = "\d+\.\d+\.\d+"$' "__version__ = `"$Version`"") -or $bumped
    if ($bumped) {
        Write-Host "Golden skripty..."
        Invoke-Native $python @("-m", "pytest", "-q", "--update-golden") -Quiet | Out-Null
    } else {
        Write-Host "  už je $Version"
    }
}

# 5. Build (pustí i celou testovou sadu).
$exe = Join-Path $root "dist\MikrotikQuickSetPlus.exe"
if (-not $SkipBuild) {
    & (Join-Path $root "build.ps1")
    if ($LASTEXITCODE -ne 0) { throw "build.ps1 selhal" }
}
if (-not (Test-Path $exe)) { throw "Chybí $exe -- postavte bez -SkipBuild" }

# 6. Text vydání z changelogu.
$notes = Join-Path ([System.IO.Path]::GetTempPath()) "mqs-release-$Version.md"
Invoke-Native $python @(
    "-m", "tools.release_notes",
    "--version", $Version,
    "--asset", $exe,
    "--output", $notes
)
$title = (Invoke-Native $python @("-m", "tools.release_notes", "--version", $Version, "--title") -Quiet) -join ""

if ($DryRun) {
    Write-Host ""
    Write-Host "--- $title ---"
    Get-Content -Raw -Encoding UTF8 $notes | Out-Host
    Write-Host "--- konec ---"
    Write-Host ""
    Write-Host "DryRun: nic se necommitlo, nepushlo ani nevydalo."
    $after = Invoke-Native "git" @("status", "--porcelain") -Quiet
    if ($after) { Write-Host "Ve stromu zůstala změna verze -- zahoďte ji, nebo vydejte bez -DryRun." }
    exit 0
}

# 7. Commit, tag, push.
if (-not $Resync) {
    $staged = Invoke-Native "git" @("status", "--porcelain") -Quiet
    if ($staged) {
        Invoke-Native "git" @("add", "-A", "pyproject.toml", "src/mqs/__init__.py", "tests/golden") -Quiet | Out-Null
        Invoke-Native "git" @("commit", "-m", "Call it a release: $Version") -Quiet | Out-Null
        Write-Host "Commit verze hotový"
    }
    Invoke-Native "git" @("tag", "-a", $tag, "-m", $title) -Quiet | Out-Null
    Invoke-Native "git" @("push", "origin", "HEAD") -Quiet | Out-Null
    Invoke-Native "git" @("push", "origin", $tag) -Quiet | Out-Null
    Write-Host "Pushnuto: HEAD i $tag"
}

# 8. Vydání na GitHubu.
if ($Resync) {
    Invoke-Native "gh" @("release", "edit", $tag, "--notes-file", $notes, "--title", $title)
    Write-Host "Text vydání $tag přepsán podle changelogu"
} else {
    Invoke-Native "gh" @("release", "create", $tag, "--title", $title, "--notes-file", $notes, $exe)
}
$url = (Invoke-Native "gh" @("release", "view", $tag, "--json", "url", "-q", ".url") -Quiet) -join ""
Write-Host "Hotovo: $url"
