# Installs RoboGame Gamemaster on Windows: the app, its Python parts,
# cloudflared, Start menu and desktop shortcuts, and on Windows 11 also
# ROS 2 Lyrical (the official binary, with pixi). Run it again to update.
# In PowerShell:
#
#   irm https://raw.githubusercontent.com/tommynugen/Robogame-Tommy/main/install.ps1 | iex
#
# or double-click install-windows.cmd. No admin rights needed.
#
# With ROS (Windows 11) the robot console works too, for robots whose
# bridge or sim runs on this computer or another one on the same network.
# Gazebo and gamepads need Linux. Without ROS (Windows 10, or
# $env:ROBOGAME_NO_ROS = 1 first) the app has the arena editor, Setup,
# Vision, Teams, the 2D chase runs and Logs.
# ROS goes in C:\dev\lyrical (short, as Windows paths must be for it).
# Saved layouts live in %LOCALAPPDATA%\RoboGame\layouts and survive updates.
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

# ROBOGAME_REPO and ROBOGAME_REF pick another repo or branch, as the
# installer test does.
$Ref = if ($env:ROBOGAME_REF) { $env:ROBOGAME_REF } else { "main" }
$Repo = if ($env:ROBOGAME_REPO) { $env:ROBOGAME_REPO } else { "tommynugen/Robogame-Tommy" }
$Zip = "https://github.com/$Repo/archive/refs/heads/$Ref.zip"
$Ros = if ($env:ROBOGAME_ROS_DIR) { $env:ROBOGAME_ROS_DIR } else { "C:\dev\lyrical" }
# ROS 2 Lyrical supports Windows 11 only (build 22000 and up).
$Win11 = [Environment]::OSVersion.Version.Build -ge 22000
$WithRos = if ($env:ROBOGAME_NO_ROS) { $false } elseif ($env:ROBOGAME_ROS) { $true } else { $Win11 }
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

# -- ROS 2 Lyrical, the official Windows binary with pixi -------------------
if ($WithRos) {
    Step "ROS 2 Lyrical (in $Ros; a large download, give it a while)"
    $pixiBin = Join-Path $env:USERPROFILE ".pixi\bin"
    if (-not (Has "pixi")) {
        if (-not (Test-Path (Join-Path $pixiBin "pixi.exe"))) {
            Invoke-RestMethod https://pixi.sh/install.ps1 | Invoke-Expression
        }
        $env:PATH = "$pixiBin;$env:PATH"
    }
    if (-not (Test-Path (Join-Path $Ros "local_setup.bat"))) {
        # GitHub's API allows 60 calls an hour per address without a token;
        # a GITHUB_TOKEN, as CI has, lifts that.
        $headers = @{}
        if ($env:GITHUB_TOKEN) { $headers["Authorization"] = "Bearer $env:GITHUB_TOKEN" }
        $releases = Invoke-RestMethod -Headers $headers "https://api.github.com/repos/ros2/ros2/releases?per_page=50"
        $asset = $releases | Where-Object { $_.tag_name -like "release-lyrical-*" } |
            ForEach-Object { $_.assets } |
            Where-Object { $_.name -like "*windows*amd64*.zip" -and $_.name -notlike "*debug*" } |
            Select-Object -First 1
        if (-not $asset) { throw "No ROS 2 Lyrical Windows download found on github.com/ros2/ros2/releases." }
        Write-Host "Downloading $($asset.name)"
        $rosZip = Join-Path $env:TEMP $asset.name
        Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $rosZip -UseBasicParsing
        $rosUnpack = Join-Path $env:TEMP "ros2-unpack"
        if (Test-Path $rosUnpack) { Remove-Item -Recurse -Force $rosUnpack }
        New-Item -ItemType Directory -Force -Path $rosUnpack | Out-Null
        tar -xf $rosZip -C $rosUnpack
        if ($LASTEXITCODE -ne 0) { throw "Could not unpack $($asset.name)." }
        $top = Get-ChildItem $rosUnpack -Recurse -Depth 2 -Filter local_setup.bat | Select-Object -First 1
        if (-not $top) { throw "No local_setup.bat in $($asset.name)." }
        New-Item -ItemType Directory -Force -Path (Split-Path $Ros) | Out-Null
        Move-Item $top.DirectoryName $Ros
        Remove-Item -Force $rosZip
    }
    $manifest = Join-Path $Ros "pixi.toml"
    Push-Location $Ros
    pixi install --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) { Pop-Location; throw "pixi install failed in $Ros." }
    if (Test-Path "preinstall_setup_windows.py") {
        pixi run --manifest-path $manifest python preinstall_setup_windows.py
    }
    # The pixi environment as a batch file, so the app starts without pixi.
    pixi shell-hook --manifest-path $manifest --shell cmd | Set-Content -Encoding ASCII (Join-Path $Ros "pixi_env.bat")
    Pop-Location

    Step "Build with ROS"
    $build = Join-Path $Ros "robogame_build.bat"
    @"
@echo off
call "$Ros\pixi_env.bat"
call "$Ros\local_setup.bat"
python -m pip install -q pywebview opencv-python
where colcon >nul 2>nul || python -m pip install -q colcon-common-extensions
cd /d "$App"
colcon build --packages-select robogame_sim
"@ | Set-Content -Encoding ASCII $build
    cmd /c "`"$build`""
    if ($LASTEXITCODE -ne 0) { throw "The build with ROS failed; see above." }
}

# -- launcher and shortcuts -------------------------------------------------
Step "Shortcuts"
$Icon = Join-Path $App "scripts\desktop\robogame-gamemaster.ico"
if ($WithRos) {
    # A batch file that sets up pixi, ROS and the build, then starts the app.
    $Launcher = Join-Path $Home_ "gamemaster.bat"
    @"
@echo off
set ROBOGAME_LAYOUTS=$Layouts
call "$Ros\pixi_env.bat"
call "$Ros\local_setup.bat"
call "$App\install\local_setup.bat"
cd /d "$App\src\robogame_sim"
python -m robogame_sim.gamemaster %*
"@ | Set-Content -Encoding ASCII $Launcher
    $Target = Join-Path $env:SystemRoot "System32\cmd.exe"
    $Arguments = "/c `"`"$Launcher`"`""
    $Style = 7  # minimized: the window holds the app's output
} else {
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
    $Target = Join-Path $Venv "Scripts\pythonw.exe"
    $Arguments = "`"$Launcher`""
    $Style = 1
}
$shell = New-Object -ComObject WScript.Shell
foreach ($dir in @([Environment]::GetFolderPath("Programs"), [Environment]::GetFolderPath("Desktop"))) {
    $lnk = $shell.CreateShortcut((Join-Path $dir "RoboGame Gamemaster.lnk"))
    $lnk.TargetPath = $Target
    $lnk.Arguments = $Arguments
    $lnk.WorkingDirectory = $Home_
    $lnk.WindowStyle = $Style
    if (Test-Path $Icon) { $lnk.IconLocation = $Icon }
    $lnk.Description = "Arena editor, robot console, camera and teams for RoboGame"
    $lnk.Save()
}

Step "Done"
Write-Host "Open RoboGame Gamemaster from the Start menu or the desktop."
if ($WithRos) {
    Write-Host "With ROS 2 Lyrical: the robot console works. Gazebo and gamepads need Linux (install.sh)."
} else {
    Write-Host "Without ROS: the robot console, BLE bridge and Gazebo need ROS (Windows 11, or install.sh on Linux)."
}
