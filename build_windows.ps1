# Phantom-Cast Pro - end-to-end Windows build
# Usage:
#     pwsh -File build_windows.ps1 -Sign $true
#
# What it does:
#   1. Creates / activates the build venv with cu128 deps pinned.
#   2. Runs PyInstaller on build/DeepLiveCamPro.spec (onefolder).
#   3. Optionally code-signs the EXE with the EV cert from the secure store.
#   4. Builds the Inno Setup installer.
#   5. Optionally signs the installer.

[CmdletBinding()]
param(
    [switch]$SkipDeps,
    [switch]$Sign,
    [string]$CertSubject = "Phantom-Cast Pro",
    [string]$TimestampUrl = "http://timestamp.digicert.com",
    [string]$InnoSetupCompiler = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
)

$ErrorActionPreference = "Stop"
Set-Location -Path (Split-Path -Parent $MyInvocation.MyCommand.Path)

function Step($msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
# Run a command and FAIL HARD if it exits non-zero. PowerShell's
# $ErrorActionPreference = "Stop" only catches cmdlet errors — native exit
# codes from pip/pyinstaller/iscc are otherwise silently swallowed by
# Invoke-Expression, which is how a 4-release streak of broken builds went
# unnoticed (see: v0.0.13 release with no installer).
function Run($cmd) {
    Write-Host "    > $cmd" -ForegroundColor DarkGray
    $global:LASTEXITCODE = 0
    Invoke-Expression $cmd
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $cmd"
    }
}

# 1. venv + deps
$pip = ".\venv\Scripts\pip.exe"
$python = ".\venv\Scripts\python.exe"

# Detect a usable Python interpreter (prefer 3.11 via py launcher, then any python on PATH)
function Get-PythonLauncher {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $hasPy311 = & py -0p 2>$null | Select-String -Pattern "3\.11"
        if ($hasPy311) { return @("py", "-3.11") }
        Write-Warning "py launcher found but Python 3.11 not registered. Falling back to default 'py'."
        return @("py")
    }
    foreach ($cand in @("python3.11", "python3", "python")) {
        if (Get-Command $cand -ErrorAction SilentlyContinue) {
            $ver = & $cand --version 2>&1
            Write-Host "    Using $cand ($ver)" -ForegroundColor DarkGray
            return @($cand)
        }
    }
    throw "No Python interpreter found. Install Python 3.11 from https://www.python.org/downloads/ and tick 'Add python.exe to PATH' and 'Install py launcher'."
}

# Treat an existing venv missing python.exe as broken
if ((Test-Path venv) -and -not (Test-Path $python)) {
    Step "Removing broken venv"
    Remove-Item -Recurse -Force venv
}

if (-not (Test-Path venv)) {
    Step "Creating venv"
    $launcher = Get-PythonLauncher
    & $launcher[0] $launcher[1..($launcher.Length - 1)] -m venv venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $python)) {
        throw "venv creation failed. Verify your Python install."
    }
}

if (-not $SkipDeps) {
    Step "Installing dependencies (cu128 stack)"
    # Use `python -m pip` so pip can replace itself on Windows (pip.exe is locked while running).
    Run "$python -m pip install --upgrade pip wheel"
    Run "$pip install -r requirements.txt"
    # Lowest torch with +cu128 wheels published is 2.7.0; pair with the matching torchvision.
    Run "$pip install --extra-index-url https://download.pytorch.org/whl/cu128 torch==2.7.0+cu128 torchvision==0.22.0+cu128"
    Run "$pip install pyinstaller==6.10.0 pynvml"
}

# 2. PyInstaller
Step "Running PyInstaller"
Remove-Item -Recurse -Force dist, build/__pycache__ -ErrorAction SilentlyContinue
Run "$python -m PyInstaller build/DeepLiveCamPro.spec --clean --noconfirm"

if (-not (Test-Path "dist\DeepLiveCamPro\DeepLiveCamPro.exe")) {
    throw "PyInstaller output missing - aborting."
}

# 3. Sign EXE (optional)
if ($Sign) {
    Step "Signing EXE"
    Run "signtool sign /n `"$CertSubject`" /tr $TimestampUrl /td sha256 /fd sha256 dist\DeepLiveCamPro\DeepLiveCamPro.exe"
}

# 4. Inno Setup
Step "Building installer"
if (-not (Test-Path $InnoSetupCompiler)) {
    throw "Inno Setup compiler not found at $InnoSetupCompiler. Install from https://jrsoftware.org/isdl.php"
}

# Fetch VC++ Redistributable on demand (Microsoft forbids redistribution
# elsewhere; the .iss bundles it but only if VCRedistNeeded). Kept out of
# the repo to avoid a 25MB binary in source control. Per
# installer/prerequisites/README.txt.
$vcRedist = "installer\prerequisites\VC_redist.x64.exe"
if (-not (Test-Path $vcRedist)) {
    Step "Downloading VC++ Redistributable"
    $url = "https://aka.ms/vs/17/release/vc_redist.x64.exe"
    Write-Host "    > $url -> $vcRedist" -ForegroundColor DarkGray
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $vcRedist -UseBasicParsing
    if (-not (Test-Path $vcRedist)) {
        throw "VC++ Redistributable download failed"
    }
    $sizeMB = [math]::Round((Get-Item $vcRedist).Length / 1MB, 2)
    Write-Host "    downloaded $sizeMB MB" -ForegroundColor DarkGray
}

Run "& `"$InnoSetupCompiler`" installer\DeepLiveCamPro.iss"

# 5. Sign installer (optional)
if ($Sign) {
    Step "Signing installer"
    $installer = Get-ChildItem -Path dist\installer -Filter *.exe | Select-Object -First 1
    Run "signtool sign /n `"$CertSubject`" /tr $TimestampUrl /td sha256 /fd sha256 `"$($installer.FullName)`""
}

Step "DONE — installer is in dist\installer\"
$installers = Get-ChildItem -Path dist\installer -Filter *.exe -ErrorAction SilentlyContinue
if (-not $installers) {
    throw "Build completed but no installer .exe was produced in dist\installer\"
}
$installers | Format-Table Name, @{Name="Size (MB)"; Expression={[math]::Round($_.Length / 1MB, 2)}}
