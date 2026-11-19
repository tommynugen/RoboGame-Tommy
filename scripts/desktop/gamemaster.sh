#!/usr/bin/env bash
# Starts the RoboGame Gamemaster with ROS and this workspace sourced, in
# its own window (closing it quits the app). If it is already running,
# this just opens another window on it. Output goes to
# ~/.cache/robogame/gamemaster.log. The desktop entry runs this script.
repo="$(cd "$(dirname "$(readlink -f "$0")")/../.." && pwd)"
log="${XDG_CACHE_HOME:-$HOME/.cache}/robogame/gamemaster.log"
mkdir -p "$(dirname "$log")"

say() {
    echo "$1" >>"$log"
    command -v notify-send >/dev/null && notify-send "RoboGame Gamemaster" "$1"
}

# Already up: just show it, without waiting for ROS to source.
# app_window.py needs no ROS; it ends with 2 if it cannot show a window.
if command -v curl >/dev/null && curl -s -o /dev/null -m 1 http://localhost:8766/; then
    python3 "$repo/src/robogame_sim/robogame_sim/app_window.py" \
        http://localhost:8766/ >>"$log" 2>&1 && exit 0
    exec xdg-open http://localhost:8766/
fi

# Which ROS: $ROS_DISTRO, else the one install.sh used, else any found.
distro="${ROS_DISTRO:-}"
[ -n "$distro" ] || distro="$(cat "${XDG_CONFIG_HOME:-$HOME/.config}/robogame/ros_distro" 2>/dev/null)"
for d in "$distro" lyrical kilted jazzy humble; do
    [ -n "$d" ] && [ -f "/opt/ros/$d/setup.bash" ] && { distro="$d"; break; }
done
ros="/opt/ros/$distro/setup.bash"
echo "--- $(date) ---" >>"$log"

# No ROS: run the app from the source tree (install.sh --no-ros makes .venv).
if [ ! -f "$ros" ] || [ ! -f "$repo/install/setup.bash" ]; then
    py="$repo/.venv/bin/python"
    [ -x "$py" ] || py=python3
    if [ -f "$ros" ]; then
        say "Not built yet: run $repo/install.sh. Starting without ROS for now."
    fi
    cd "$repo/src/robogame_sim"
    exec "$py" -m robogame_sim.gamemaster >>"$log" 2>&1
fi
set +u
source "$ros"
source "$repo/install/setup.bash"
cd "$repo"
exec ros2 run robogame_sim robogame_gamemaster >>"$log" 2>&1
