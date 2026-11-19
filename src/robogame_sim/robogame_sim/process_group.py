"""Signals a child started with start_new_session=True and everything it
started, on Linux and on Windows.

Linux sends the signal to the child's process group. Windows has no
process groups or SIGINT for other processes, so there taskkill ends the
child and its children at once, whatever the signal.
"""

import os
import signal
import subprocess
import sys

# Windows has no SIGKILL; SIGTERM ends a process there all the same.
KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def signal_group(pid, sig):
    if sys.platform != "win32":
        os.killpg(pid, sig)
        return
    subprocess.run(["taskkill", "/T", "/F", "/PID", str(pid)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
