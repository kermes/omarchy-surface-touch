#!/usr/bin/env python3
"""Two jobs while the Omarchy screensaver (a terminal running omarchy-screensaver,
window class org.omarchy.screensaver) is up:

1. Hide the OSK and virtual trackpad panels -- they're on the Overlay layer,
   above the screensaver's regular toplevel window, so they'd otherwise stay
   visible on top of it.
2. Dismiss the screensaver on any touch. omarchy-screensaver only reads
   keyboard/mouse input to exit (`read -n1 -t 1` in its own loop) -- touch
   alone can't reach that, so this injects a keypress via the trackpad
   injector's virtual keyboard device instead.
"""
import glob
import os
import socket
import subprocess
import threading
import time

import evdev
from evdev import ecodes as e

TOUCH_DEVICE_NAME = os.environ.get("TOUCH_DEVICE_NAME")  # optional override
SOCKET_PATH = "/run/trackpad.sock"

# This runs as root (see the unit), so nothing about the user's session is in
# the environment and every process lookup would otherwise match all users.
RUNTIME_DIR = os.environ.get("USER_XDG_RUNTIME_DIR", "/run/user/1000")
TARGET_UID = os.environ.get("TARGET_UID")
OSK_STATE_FILE = "/tmp/osk-visible"
TRACKPAD_MARKER = "omarchy/trackpad/shell.qml"


def log(msg):
    print(msg, flush=True)


def _uid_scope():
    """Restrict pgrep/pkill to the session user, since root sees every process."""
    return ["-u", TARGET_UID] if TARGET_UID else []


def hyprctl_eval(lua):
    """Run `hyprctl eval` against the user's compositor.

    As root there is no HYPRLAND_INSTANCE_SIGNATURE in the environment, so find
    the instance on disk and pass it in -- the same approach
    two-finger-right-click.py uses. Returns False when no compositor is up yet,
    which is normal at boot: this unit is ordered after multi-user.target, not
    after the graphical session.
    """
    for sock_dir in glob.glob(f"{RUNTIME_DIR}/hypr/*"):
        try:
            r = subprocess.run(
                ["hyprctl", "eval", lua],
                env={**os.environ,
                     "HYPRLAND_INSTANCE_SIGNATURE": os.path.basename(sock_dir),
                     "XDG_RUNTIME_DIR": RUNTIME_DIR},
                capture_output=True, text=True, timeout=2,
            )
            if r.returncode == 0:
                return True
        except Exception:
            continue
    return False


def screensaver_active():
    r = subprocess.run(["pgrep", *_uid_scope(), "-f", "org.omarchy.screensaver"],
                       capture_output=True)
    return r.returncode == 0


def send_injector(cmd):
    try:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(1)
        s.connect(SOCKET_PATH)
        s.sendall((cmd + "\n").encode())
        s.close()
    except Exception as ex:
        log(f"injector send failed: {ex}")


def hide_osk_and_trackpad():
    log("screensaver activated -- hiding OSK/trackpad")
    subprocess.run(["pkill", "--signal", "USR1", *_uid_scope(), "wvkbd-deskintl"])
    try:
        os.remove(OSK_STATE_FILE)
    except FileNotFoundError:
        pass
    subprocess.run(["pkill", *_uid_scope(), "-f", TRACKPAD_MARKER])
    hyprctl_eval("hl.config({ cursor = { hide_on_touch = true } })")


def _multitouch_devices():
    """Direct-input multitouch devices, i.e. touchscreens.

    ABS_MT_SLOT alone is not enough -- a precision touchpad reports it too, so
    on a Surface with the Type Cover attached there are two matches and neither
    is unambiguous. The kernel separates them with INPUT_PROP_DIRECT (the
    surface you touch is the display) versus INPUT_PROP_POINTER (an indirect
    pointing device), which is the same signal libinput keys off.
    """
    found = []
    for path in evdev.list_devices():
        try:
            d = evdev.InputDevice(path)
        except OSError:
            continue
        caps = d.capabilities().get(e.EV_ABS) or []
        if not any(code == e.ABS_MT_SLOT for code, _ in caps):
            continue
        try:
            if e.INPUT_PROP_DIRECT not in d.input_props():
                continue
        except Exception:
            pass  # no property info from this driver; keep it as a candidate
        found.append(d)
    return found


def _all_device_names():
    names = []
    for path in evdev.list_devices():
        try:
            names.append(evdev.InputDevice(path).name)
        except OSError:
            continue
    return names


def find_touch_device():
    if TOUCH_DEVICE_NAME:
        for path in evdev.list_devices():
            try:
                d = evdev.InputDevice(path)
            except OSError:
                continue
            if d.name == TOUCH_DEVICE_NAME:
                return d
        return None
    candidates = _multitouch_devices()
    return candidates[0] if len(candidates) == 1 else None


def _report_no_device():
    """Say what was expected, what was found, and -- crucially -- why.

    The old code logged only "waiting for touch device ..." every 2s forever,
    which is indistinguishable from the service working. There are two very
    different causes and the message separates them: a device name that matches
    nothing (different Surface model), and no read access to /dev/input at all
    (which should only happen now if this ends up running unprivileged).
    """
    visible = evdev.list_devices()
    if not visible and glob.glob("/dev/input/event*"):
        log("ERROR: cannot read any /dev/input/event* node.")
        if os.geteuid() != 0:
            log("  Not running as root. This must be installed as a system unit")
            log("  (screensaver/install.sh does that) -- /dev/input/* is root:input,")
            log("  and logind hands the compositor its devices over D-Bus rather than")
            log("  through file permissions, so as your user nothing is visible here.")
        else:
            log("  Running as root but still seeing nothing, which is unexpected --")
            log("  check that the touchscreen driver is up (iptsd, hid-multitouch).")
        return

    if TOUCH_DEVICE_NAME:
        log(f"ERROR: no input device is named {TOUCH_DEVICE_NAME!r} (from TOUCH_DEVICE_NAME).")
    else:
        log("ERROR: could not identify the touchscreen automatically.")
    cands = _multitouch_devices()
    if len(cands) > 1:
        log("  more than one multitouch device found -- set TOUCH_DEVICE_NAME to one of:")
        for d in cands:
            log(f"    {d.name!r}  ({d.path})")
    elif not cands:
        log("  no direct-input multitouch device found. Is the touchscreen driver up (iptsd, hid-multitouch)?")
    names = _all_device_names()
    log("  input devices present: " + (", ".join(repr(n) for n in names) if names else "(none)"))


def wait_for_touch_device():
    reported = False
    while True:
        dev = find_touch_device()
        if dev is not None:
            log(f"using touch device {dev.name!r} ({dev.path})")
            return dev
        if not reported:
            _report_no_device()
            reported = True
        time.sleep(2)


def poll_screensaver_state():
    was_active = False
    while True:
        active = screensaver_active()
        if active and not was_active:
            hide_osk_and_trackpad()
        was_active = active
        time.sleep(1)


def watch_touch_for_dismiss():
    dev = wait_for_touch_device()
    log(f"watching {dev.path} for dismiss taps")
    for ev in dev.read_loop():
        if ev.type == e.EV_ABS and ev.code == e.ABS_MT_TRACKING_ID and ev.value != -1:
            if screensaver_active():
                log("touch detected during screensaver -- dismissing")
                send_injector("KEY esc")


def main():
    threading.Thread(target=poll_screensaver_state, daemon=True).start()
    watch_touch_for_dismiss()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
