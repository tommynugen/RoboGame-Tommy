#!/usr/bin/env bash
# Installs RoboGame Gamemaster on Linux in one go: ROS 2 and Gazebo, the
# app's other parts, this repo, the build, and an entry in the app menu.
# Run it again to update. From a fresh machine:
#
#   curl -fsSL https://raw.githubusercontent.com/tommynugen/Robogame-Tommy/main/install.sh | bash
#
# or from a checkout: ./install.sh. It asks for your password once (sudo).
#
#   Ubuntu 26.04  ROS 2 Lyrical + Gazebo Jetty     (what the sim is built on)
#   Ubuntu 24.04  ROS 2 Jazzy + Gazebo Harmonic    (no Camera view card)
#   Ubuntu 22.04  ROS 2 Humble, no Gazebo sim      (editor, console, robots)
#   Arch Linux    ROS 2 from arch4edu, no Gazebo   (editor, console, robots)
#                 or --box: Ubuntu 26.04 in a distrobox (the same as 26.04)
#
# Options:
#   --ros DISTRO       pick the ROS 2 distro yourself (lyrical, kilted, jazzy, humble)
#   --box              ROS and Gazebo in an Ubuntu 26.04 distrobox (Arch, if
#                      arch4edu has no ROS 2, Fedora, any Linux with distrobox)
#   --no-ros           no ROS at all: the app in a Python venv, for any Linux
#                      (editor, vision, teams and chase tabs; no robot console)
#   --dir PATH         where to put the repo (default ~/Robogame-Tommy)
#   --no-cloudflared   skip cloudflared (the Teams tab's public sign-up link)
#   --no-menu          skip the app menu entry
set -euo pipefail

REPO_URL="https://github.com/tommynugen/Robogame-Tommy.git"
BOX=robogame
BOX_IMAGE=docker.io/library/ubuntu:26.04
dir=""
distro=""
no_ros=0
box=0
cloudflared=1
menu=1
while [ $# -gt 0 ]; do
    case "$1" in
        --ros) distro="$2"; shift ;;
        --no-ros) no_ros=1 ;;
        --box) box=1 ;;
        --dir) dir="$2"; shift ;;
        --no-cloudflared) cloudflared=0 ;;
        --no-menu) menu=0 ;;
        -h|--help) sed -n '2,27p' "$0" 2>/dev/null || true; exit 0 ;;
        *) echo "Unknown option $1 (try --help)"; exit 2 ;;
    esac
    shift
done

step() { printf '\n\033[1;36m== %s\033[0m\n' "$1"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$1"; }
die() { printf '\033[1;31m%s\033[0m\n' "$1"; exit 1; }
need_sudo() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo "$@"; fi; }
has() { command -v "$1" >/dev/null; }

# Package manager: apt (Ubuntu, Debian), pacman (Arch) or dnf (Fedora).
if has apt-get; then pm=apt
elif has pacman; then pm=pacman
elif has dnf; then pm=dnf
else pm=""
fi
pkg_install() {
    case "$pm" in
        apt) need_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
        pacman) need_sudo pacman -S --needed --noconfirm "$@" ;;
        dnf) need_sudo dnf install -y "$@" ;;
        *) return 1 ;;
    esac
}
# Fresh package lists. Arch wants a full upgrade rather than a bare -Sy.
pkg_refresh() {
    case "$pm" in
        apt) need_sudo apt-get update -qq ;;
        pacman) need_sudo pacman -Syu --noconfirm ;;
    esac
}
# Optional packages: one at a time, so a missing one does not stop the rest.
pkg_maybe() { for p in "$@"; do pkg_install "$p" >/dev/null 2>&1 || warn "  ($p not available here, skipped)"; done; }

# -- which system, which ROS --------------------------------------------------
. /etc/os-release 2>/dev/null || true
codename="${UBUNTU_CODENAME:-${VERSION_CODENAME:-}}"
case " ${ID:-} ${ID_LIKE:-} " in *" arch "*) is_arch=1 ;; *) is_arch=0 ;; esac
if [ "$no_ros" = 0 ] && [ "$box" = 0 ] && [ -z "$distro" ]; then
    case "${ID:-}:${VERSION_ID:-}" in
        ubuntu:26.04) distro=lyrical ;;
        ubuntu:24.04) distro=jazzy ;;
        ubuntu:22.04) distro=humble ;;
        *) if [ "$is_arch" = 1 ]; then arch_ros=1
           else die "No ROS 2 packages for ${PRETTY_NAME:-this system}. Run again with --box (ROS in an Ubuntu distrobox) or --no-ros (the app without ROS)."
           fi ;;
    esac
fi
arch_ros="${arch_ros:-0}"

# Arch: ROS 2 binaries from arch4edu (github.com/arch4edu/arch4edu), the
# newest distro it has. If it has none, ROS goes in a distrobox instead.
if [ "$arch_ros" = 1 ]; then
    step "arch4edu (ROS 2 for Arch)"
    if ! grep -q '^\[arch4edu\]' /etc/pacman.conf; then
        need_sudo pacman-key --init >/dev/null 2>&1 || true
        if ! need_sudo pacman-key --recv-keys 7931B6D628C8D3BA; then
            # No key server: the keyring package, checked against its hash.
            curl -fsSL -o /tmp/arch4edu-keyring.pkg.tar.zst \
                https://mirrors.tuna.tsinghua.edu.cn/arch4edu/any/arch4edu-keyring-20200805-1-any.pkg.tar.zst
            echo "a6abbb16e57bb9065689f5b5391c945e35e256f2e6dbfa11476fdfe880f72775  /tmp/arch4edu-keyring.pkg.tar.zst" | sha256sum -c -
            need_sudo pacman -U --noconfirm /tmp/arch4edu-keyring.pkg.tar.zst
        fi
        need_sudo pacman-key --lsign-key 7931B6D628C8D3BA
        printf '\n[arch4edu]\nServer = https://repository.arch4edu.org/$arch\nServer = https://mirrors.cicku.me/arch4edu/$arch\n' \
            | need_sudo tee -a /etc/pacman.conf >/dev/null
    fi
    pkg_refresh
    available="$(pacman -Sl arch4edu 2>/dev/null | awk '{print $2}')"
    for d in lyrical kilted jazzy humble; do
        for name in "ros2-$d-base" "ros2-$d"; do
            if echo "$available" | grep -qx "$name"; then distro=$d; ros_pkg=$name; break 2; fi
        done
    done
    if [ -z "$distro" ]; then
        warn "arch4edu has no ROS 2 right now; putting ROS in an Ubuntu distrobox instead."
        arch_ros=0; box=1
    fi
fi
[ "$box" = 1 ] && no_ros=0
case "$distro" in
    lyrical|kilted|jazzy) [ "$arch_ros" = 1 ] && sim=0 || sim=1 ;;
    *) sim=0 ;;
esac
arch="$(dpkg --print-architecture 2>/dev/null || uname -m)"

if [ "$box" = 1 ]; then
    echo "Installing RoboGame Gamemaster with ROS 2 Lyrical in an Ubuntu 26.04 distrobox."
elif [ "$no_ros" = 1 ]; then
    echo "Installing RoboGame Gamemaster without ROS."
else
    echo "Installing RoboGame Gamemaster on ${PRETTY_NAME:-Linux} with ROS 2 ${distro^}."
    if [ "$arch_ros" = 1 ]; then
        warn "No Gazebo sim from arch4edu; run with --box for ROS and Gazebo in an Ubuntu distrobox."
    else
        [ "$sim" = 1 ] || warn "The Gazebo sim needs Ubuntu 24.04 or newer; Humble gets the app without it."
        [ "$pm" = apt ] || die "ROS 2 packages need Ubuntu; use --box or --no-ros here."
    fi
fi

get_repo() {
    step "RoboGame repo"
    here="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
    if [ -z "$dir" ] && [ -n "$here" ] && [ -d "$here/src/robogame_sim" ]; then
        dir="$here"
    fi
    dir="${dir:-$HOME/Robogame-Tommy}"
    if [ -d "$dir/.git" ]; then
        git -C "$dir" pull --ff-only || warn "Could not update $dir (local changes?); building it as it is."
    elif [ -d "$dir/src/robogame_sim" ]; then
        echo "Using $dir as it is (not a git checkout, so not updated)."
    else
        git clone "$REPO_URL" "$dir"
    fi
    cd "$dir"
}

# -- distrobox: Ubuntu 26.04 with ROS inside, sharing home, screen and USB -----
if [ "$box" = 1 ]; then
    step "distrobox"
    pkg_refresh
    has git || pkg_install git
    if ! has distrobox || ! { has podman || has docker; }; then
        pkg_install distrobox podman || die "Install distrobox and podman, then run this again."
    fi
    get_repo
    if ! distrobox list 2>/dev/null | grep -qw "$BOX"; then
        distrobox create --yes --name "$BOX" --image "$BOX_IMAGE"
    fi
    flags=(--dir "$dir" --no-menu)
    [ "$cloudflared" = 1 ] || flags+=(--no-cloudflared)
    # The box shares home, so the repo and its build are the same folder.
    distrobox enter "$BOX" -- bash "$dir/install.sh" "${flags[@]}"
    if [ "$menu" = 1 ]; then
        step "App menu"
        ROBOGAME_LAUNCH_PREFIX="distrobox enter $BOX --" bash "$dir/scripts/desktop/install.sh"
    fi
    step "Done"
    echo "Open RoboGame Gamemaster from the app menu, or run:"
    echo "  distrobox enter $BOX -- $dir/scripts/desktop/gamemaster.sh"
    exit 0
fi

# -- system packages ---------------------------------------------------------
step "System packages"
case "$pm" in
    apt) pkg_refresh
         pkg_install git curl ca-certificates python3 python3-venv python3-pip
         pkg_maybe python3-numpy python3-opencv python3-webview libnotify-bin ;;
    pacman) pkg_refresh
            pkg_install git curl python python-pip
            pkg_maybe python-numpy libnotify ;;
    dnf) pkg_install git curl python3 python3-pip
         pkg_maybe python3-numpy python3-opencv python3-pywebview libnotify ;;
    *) has git && has python3 || die "Install git and python3, then run this again." ;;
esac

# -- ROS 2 and Gazebo --------------------------------------------------------
if [ "$arch_ros" = 1 ]; then
    step "ROS 2 ${distro^} (arch4edu)"
    pkg_install "$ros_pkg"
    pkg_maybe python-colcon-common-extensions "ros2-$distro-joy"
elif [ "$no_ros" = 0 ]; then
    step "ROS 2 ${distro^}"
    # Add the ROS apt source unless this machine already has one.
    if ! dpkg -s ros2-apt-source >/dev/null 2>&1 \
        && ! grep -rqs packages.ros.org /etc/apt/sources.list.d/; then
        pkg_install software-properties-common
        need_sudo add-apt-repository -y universe
        tag="$(curl -fsSL https://api.github.com/repos/ros-infrastructure/ros-apt-source/releases/latest \
               | grep -F '"tag_name"' | cut -d'"' -f4)"
        [ -n "$tag" ] || die "Could not look up the ROS apt source on GitHub."
        curl -fsSL -o /tmp/ros2-apt-source.deb \
            "https://github.com/ros-infrastructure/ros-apt-source/releases/download/$tag/ros2-apt-source_$tag.${codename}_all.deb"
        need_sudo dpkg -i /tmp/ros2-apt-source.deb
        need_sudo apt-get update -qq
    fi
    pkgs="ros-dev-tools ros-$distro-ros-base ros-$distro-joy"
    [ "$sim" = 1 ] && pkgs="$pkgs ros-$distro-ros-gz"
    # shellcheck disable=SC2086
    pkg_install $pkgs
    # Qt 6 for the Camera view card in Gazebo Jetty's side panel.
    [ "$distro" = lyrical ] && pkg_maybe qt6-base-dev qt6-declarative-dev
fi

# -- cloudflared (Teams tab's public sign-up link) ---------------------------
if [ "$cloudflared" = 1 ] && ! has cloudflared; then
    step "cloudflared"
    case "$arch" in
        amd64|x86_64) cf=amd64 ;;
        arm64|aarch64) cf=arm64 ;;
        *) cf="" ;;
    esac
    if [ "$pm" = pacman ]; then
        pkg_install cloudflared || warn "cloudflared did not install; the Teams tab still works on the local network."
    elif [ -n "$cf" ] && [ "$pm" = apt ] && curl -fsSL -o /tmp/cloudflared.deb \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$cf.deb"; then
        need_sudo dpkg -i /tmp/cloudflared.deb || warn "cloudflared did not install; the Teams tab still works on the local network."
    elif [ -n "$cf" ] && mkdir -p "$HOME/.local/bin" && curl -fsSL -o "$HOME/.local/bin/cloudflared" \
        "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$cf"; then
        chmod +x "$HOME/.local/bin/cloudflared"
    else
        warn "No cloudflared for $arch; the Teams tab still works on the local network."
    fi
fi

get_repo

# -- build -------------------------------------------------------------------
if [ "$no_ros" = 0 ]; then
    step "Build"
    set +u
    # shellcheck disable=SC1090
    source "/opt/ros/$distro/setup.bash"
    set -u
    # The Camera view card (robogame_gui) needs Gazebo Jetty's Qt 6; on
    # anything else, or if it fails, build the rest without it.
    if [ "$distro" != lyrical ] || [ "$sim" = 0 ] || ! colcon build --symlink-install; then
        [ "$distro" = lyrical ] && [ "$sim" = 1 ] && warn "The Camera view card did not build; building the rest without it."
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
