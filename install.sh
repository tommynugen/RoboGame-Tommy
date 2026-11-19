#!/usr/bin/env bash
# Installs RoboGame Gamemaster on Ubuntu in one go: ROS 2 and Gazebo for
# this Ubuntu, the app's other parts, this repo, the build, and an entry
# in the app menu. Run it again to update. From a fresh machine:
#
#   curl -fsSL https://raw.githubusercontent.com/tommynugen/RoboGames-Fall-2026/main/install.sh | bash
#
# or from a checkout: ./install.sh. It asks for your password once (sudo).
#
#   Ubuntu 26.04  ROS 2 Lyrical + Gazebo Jetty     (what the sim is built on)
#   Ubuntu 24.04  ROS 2 Jazzy + Gazebo Harmonic    (no Camera view card)
#   Ubuntu 22.04  ROS 2 Humble, no Gazebo sim      (editor, console, robots)
#
# Options:
#   --ros DISTRO       pick the ROS 2 distro yourself (lyrical, kilted, jazzy, humble)
#   --no-ros           no ROS at all: the app in a Python venv, for any Linux
#                      (editor, vision, teams and chase tabs; no robot console)
#   --dir PATH         where to put the repo (default ~/RoboGames-Fall-2026)
#   --no-cloudflared   skip cloudflared (the Teams tab's public sign-up link)
#   --no-menu          skip the app menu entry
set -euo pipefail

REPO_URL="https://github.com/tommynugen/RoboGames-Fall-2026.git"
dir=""
distro=""
no_ros=0
cloudflared=1
menu=1
while [ $# -gt 0 ]; do
    case "$1" in
        --ros) distro="$2"; shift ;;
        --no-ros) no_ros=1 ;;
        --dir) dir="$2"; shift ;;
        --no-cloudflared) cloudflared=0 ;;
        --no-menu) menu=0 ;;
        -h|--help) sed -n '2,24p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unknown option $1 (try --help)"; exit 2 ;;
    esac
    shift
done

step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$1"; }
die() { printf '\033[1;31m%s\033[0m\n' "$1"; exit 1; }

# -- which Ubuntu, which ROS ------------------------------------------------
. /etc/os-release 2>/dev/null || true
codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
if [ "$no_ros" = 0 ] && [ -z "$distro" ]; then
    case "${ID:-}:${VERSION_ID:-}" in
        ubuntu:26.04) distro=lyrical ;;
        ubuntu:24.04) distro=jazzy ;;
        ubuntu:22.04) distro=humble ;;
        *) die "No ROS 2 for ${PRETTY_NAME:-this system} here. Use Ubuntu 22.04, 24.04 or 26.04, or run again with --no-ros for the app without ROS." ;;
    esac
fi
case "$distro" in
    lyrical|kilted|jazzy) sim=1 ;;
    *) sim=0 ;;
esac
arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"

if [ "$no_ros" = 1 ]; then
    echo "Installing RoboGame Gamemaster without ROS."
else
    echo "Installing RoboGame Gamemaster on ${PRETTY_NAME:-Linux} with ROS 2 ${distro^}."
    [ "$sim" = 1 ] || warn "The Gazebo sim needs Ubuntu 24.04 or newer; Humble gets the app without it."
fi

need_sudo() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
apt_get() { need_sudo env DEBIAN_FRONTEND=noninteractive apt-get "$@"; }
# Optional packages: one at a time, so a missing one does not stop the rest.
apt_maybe() { for p in "$@"; do apt_get install -y "$p" >/dev/null 2>&1 || warn "  ($p not available here, skipped)"; done; }

# -- system packages ---------------------------------------------------------
step "System packages"
apt_get update -qq
apt_get install -y git curl ca-certificates python3 python3-venv python3-pip
apt_maybe python3-numpy python3-opencv python3-webview libnotify-bin

# -- ROS 2 and Gazebo --------------------------------------------------------
if [ "$no_ros" = 0 ]; then
    step "ROS 2 ${distro^}"
    # Add the ROS apt source unless this machine already has one.
    if ! dpkg -s ros2-apt-source >/dev/null 2>&1 \
        && ! grep -rqs packages.ros.org /etc/apt/sources.list.d/; then
        apt_get install -y software-properties-common
        need_sudo add-apt-repository -y universe
        tag="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
               | grep -F '"tag_name"' | cut -d'"' -f4)"
        [ -n "$tag" ] || die "Could not look up the ROS apt source on GitHub."
        curl -fsSL -o /tmp/ros2-apt-source.deb \
            "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$tag/ros2-apt-source_$tag.${codename}_all.deb"
        need_sudo dpkg -i /tmp/ros2-apt-source.deb
        apt_get update -qq
    fi
    pkgs="ros-dev-tools ros-$distro-ros-base ros-$distro-joy"
    [ "$sim" = 1 ] && pkgs="$pkgs ros-$distro-ros-gz"
    # shellcheck disable=SC2086
    apt_get install -y $pkgs
    # Qt 6 for the Camera view card in Gazebo Jetty's side panel.
    [ "$distro" = lyrical ] && apt_maybe qt6-base-dev qt6-declarative-dev
fi

# -- cloudflared (Teams tab's public sign-up link) ---------------------------
if [ "$cloudflared" = 1 ] && ! command -v cloudflared >/dev/null; then
    step "cloudflared"
    case "$arch" in
        amd64|x86_64) cf=amd64 ;;
        arm64|aarch64) cf=arm64 ;;
        *) cf="" ;;
    esac
    if [ -n "$cf" ] && curl -fsSL -o /tmp/cloudflared.deb \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$cf.deb"; then
        need_sudo dpkg -i /tmp/cloudflared.deb || warn "cloudflared did not install; the Teams tab still works on the local network."
    else
        warn "No cloudflared for $arch; the Teams tab still works on the local network."
    fi
fi

# -- the repo ----------------------------------------------------------------
step "RoboGame repo"
here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -z "$dir" ] && [ -n "$here" ] && [ -d "$here/src/robogame_sim" ]; then
    dir="$here"
fi
dir="${dir:-$HOME/RoboGames-Fall-2026}"
if [ -d "$dir/.git" ]; then
    git -C "$dir" pull --ff-only || warn "Could not update $dir (local changes?); building it as it is."
elif [ -d "$dir/src/robogame_sim" ]; then
    echo "Using $dir as it is (not a git checkout, so not updated)."
else
    git clone "$REPO_URL" "$dir"
fi
cd "$dir"

# -- build -------------------------------------------------------------------
if [ "$no_ros" = 0 ]; then
    step "Build"
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/$distro/setup.bash"
    set -u
    # The Camera view card (robogame_gui) needs Gazebo Jetty's Qt 6; on
    # anything else, or if it fails, build the rest without it.
    if [ "$distro" != lyrical ] || ! colcon build --symlink-install; then
        [ "$distro" = lyrical ] && warn "The Camera view card did not build; building the rest without it."
        colcon build --symlink-install --packages-skip robogame_gui
    fi
    mkdir -p "$HOME/.config/robogame"
    echo "$distro" >"$HOME/.config/robogame/ros_distro"
else
    step "Python venv"
    venv="$dir/.venv"
    python3 -m venv --system-site-packages "$venv"
    "$venv/bin/pip" install -q --upgrade pip
    "$venv/bin/pip" install -q numpy opencv-python pywebview || \
        "$venv/bin/pip" install -q numpy opencv-python
fi

# -- app menu ----------------------------------------------------------------
if [ "$menu" = 1 ]; then
    step "App menu"
    bash "$dir/scripts/desktop/install.sh"
fi

step "Done"
echo "Open RoboGame Gamemaster from the app menu, or run:"
echo "  $dir/scripts/desktop/gamemaster.sh"
[ "$no_ros" = 1 ] && echo "(Without ROS: the robot console and BLE bridge tabs stay off.)"
exit 0
