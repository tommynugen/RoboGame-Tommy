# Installs RoboGame Gamemaster on Windows 10 or 11, without ROS: Python
# (if missing), the app, its Python parts and cloudflared, and a Start
# menu and desktop shortcut. Run it again to update. In PowerShell:
#
#   irm https://raw.githubusercontent.com/tommynugen/RoboGames-Fall-2026/main/install.ps1 | iex
#
# or double-click install-windows.cmd. No admin rights needed.
#
# Without ROS the app has the arena editor, Setup, Vision (USB camera and
# AprilTags), Teams (phone sign-up), the 2D chase runs and Logs. The robot
# console, BLE bridge and Gazebo sim need ROS 2 on Ubuntu (install.sh).
# Saved layouts live in %LOCALAPPDATA%\RoboGame\layouts and survive updates.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ROBOGAME_REF picks another branch, as the installer test does.
$Ref = if ($env:ROBOGAME_REF) { $env:ROBOGAME_REF } else { "main" }
$Zip = "https://github.com/tommynugen/RoboGames-Fall-2026/archive/refs/heads/$Ref.zip"
$Home_ = Join-Path $env:LOCALAPPDATA "RoboGame"
$App = Join-Path $Home_ "app"
$Venv = Join-Path $Home_ "venv"
$Layouts = Join-Path $Home_ "layouts"

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Has($cmd) { [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# -- Python 3.10 or newer ---------------------------------------------------
Step "Python"
function Find-Python {
    $ask = "import sys; print(sys.executable if sys.version_info >= (3, 10) else '')"
    foreach ($try in @("py", "python")) {
        if (-not (Has $try)) { continue }
        if ($try -eq "py") { $exe = & py -3 -c $ask 2>$null } else { $exe = & python -c $ask 2>$null }
        if ($LASTEXITCODE -eq 0 -and $exe) { return "$exe".Trim() }
    }
    $own = Get-ChildItem "$env:LOCALAPPDATA\Programs\Python\Python3*\python.exe" -ErrorAction SilentlyContinue |
        Sort-Object FullName -Descending | Select-Object -First 1
    if ($own) { return $own.FullName }
    return $null
}
$Python = Find-Python
if (-not $Python) {
    if (-not (Has "winget")) { throw "No Python 3.10+ and no winget. Install Python from python.org, then run this again." }
    winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements | Out-Null
    $Python = Find-Python
    if (-not $Python) { throw "Python did not install. Install it from python.org, then run this again." }
}
Write-Host "Using $Python"

# -- the app ----------------------------------------------------------------
Step "RoboGame Gamemaster"
New-Item -ItemType Directory -Force -Path $Home_, $Layouts | Out-Null
$tmp = Join-Path $env:TEMP "robogame-main.zip"
Invoke-WebRequest -Uri $Zip -OutFile $tmp -UseBasicParsing
$unpack = Join-Path $env:TEMP "robogame-unpack"
if (Test-Path $unpack) { Remove-Item -Recurse -Force $unpack }
Expand-Archive -Path $tmp -DestinationPath $unpack
if (Test-Path $App) { Remove-Item -Recurse -Force $App }
Move-Item (Get-ChildItem $unpack | Select-Object -First 1).FullName $App
Remove-Item -Force $tmp

# -- Python parts -----------------------------------------------------------
Step "Python parts (numpy, OpenCV, pywebview)"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { & $Python -m venv $Venv }
$VPy = Join-Path $Venv "Scripts\python.exe"
& $VPy -m pip install -q --upgrade pip
& $VPy -m pip install -q numpy opencv-python pywebview
if ($LASTEXITCODE -ne 0) { throw "pip could not install the app's parts." }

# -- cloudflared (Teams tab's public sign-up link) --------------------------
Step "cloudflared"
if (Has "cloudflared") { Write-Host "Already there." }
elseif (Has "winget") {
    winget install -e --id Cloudflare.cloudflared --silent --accept-package-agreements --accept-source-agreements | Out-Null
    if ($LASTEXITCODE -ne 0) { Write-Host "cloudflared did not install; the Teams tab still works on the local network." -ForegroundColor Yellow }
} else { Write-Host "No winget, skipped; the Teams tab still works on the local network." -ForegroundColor Yellow }

# -- launcher and shortcuts -------------------------------------------------
Step "Shortcuts"
$Launcher = Join-Path $Home_ "gamemaster.pyw"
@"
import os, sys
os.environ.setdefault("ROBOGAME_LAYOUTS", r"$Layouts")
src = r"$App\src\robogame_sim"
sys.path.insert(0, src)
os.chdir(src)
from robogame_sim import gamemaster
sys.exit(gamemaster.main())
"@ | Set-Content -Encoding UTF8 $Launcher

$VPyw = Join-Path $Venv "Scripts\pythonw.exe"
$Icon = Join-Path $App "scripts\desktop\robogame-gamemaster.ico"
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "RoboGame Gamemaster.lnk"))
    $lnk.TargetPath = $VPyw
    $lnk.Arguments = "`"$Launcher`""
    $lnk.WorkingDirectory = $Home_
    if (Test-Path $Icon) { $lnk.IconLocation = $Icon }
    $lnk.Description = "Arena editor, camera and teams for RoboGame"
    $lnk.Save()
}

Step "Done"
Write-Host "Open RoboGame Gamemaster from the Start menu or the desktop."
Write-Host "The robot console, BLE bridge and Gazebo sim need ROS 2 on Ubuntu (install.sh)."
