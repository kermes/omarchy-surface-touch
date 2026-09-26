#!/usr/bin/env python3
"""Run an on-screen keyboard only while the physical keyboard is unusable.

Two distinct conditions, and device presence alone only sees one of them:

  * the Type Cover is detached, and
  * the Type Cover is folded back behind the screen -- still attached, still
    enumerated, so a presence check misses the most common tablet posture.

Fold-back is reported as SW_TABLET_MODE on an EV_SW device. Where that device
lives differs by model, which is why nothing here matches on a device name:

  * Surface Go: chassis-level ("Intel HID switches"). It persists when the
    cover is detached, so the two conditions stay independent.
  * Surface Pro 7+: provided by the Type Cover itself ("Microsoft Surface Type
    Cover Tablet Mode Switch", a name shared by ten kernel devices there, only
    one of which exposes SW_TABLET_MODE). Detaching removes the switch, so the
    two conditions collapse into one.

A missing switch therefore means "no cover", not "not in tablet mode" -- which
the predicate below already handles, since it asks whether a usable keyboard is
present rather than trusting the switch alone.

Runs as root from a system unit so it can read /dev/input directly, and starts
the keyboard as the session user with setpriv.
"""

import glob
import os
import pwd
import select
import shlex
import signal
import subprocess
import sys
from ctypes import CDLL, c_int, c_char_p, c_uint32, get_errno

import evdev
from evdev import ecodes as e

OSK_COMMAND = os.environ.get("OSK_COMMAND", "wvkbd-deskintl --hidden -l full,special")
TARGET_UID = int(os.environ.get("TARGET_UID", "1000"))
# Not TARGET_UID: a primary group is not reliably the same id as the user
# (useradd -g users gives the shared users group).
try:
    TARGET_GID = pwd.getpwuid(TARGET_UID).pw_gid
except KeyError:
    TARGET_GID = TARGET_UID
RUNTIME_DIR = os.environ.get("USER_XDG_RUNTIME_DIR", f"/run/user/{TARGET_UID}")

# Buses a keyboard can actually be detached from. An x86 machine enumerates an
# "AT Translated Set 2 keyboard" on i8042 whether or not anything is plugged
# into it, so matching alphabetic keys alone reports a keyboard that is not
# there.
HOTPLUG_BUSES = (0x03, 0x05)  # BUS_USB, BUS_BLUETOOTH
ALPHA_KEYS = {e.KEY_A, e.KEY_Q, e.KEY_Z}


def log(*a):
    print(*a, flush=True)


def _devices():
    for path in evdev.list_devices():
        try:
            yield evdev.InputDevice(path)
        except OSError:
            continue


def find_tablet_switch():
    """The one EV_SW device exposing SW_TABLET_MODE, by capability not name."""
    for dev in _devices():
        caps = dev.capabilities().get(e.EV_SW) or []
        if e.SW_TABLET_MODE in caps:
            return dev
    return None


def read_tablet_mode(dev):
    """Current switch state. EVIOCGSW, because events only report changes."""
    import array
    import fcntl
    buf = array.array("B", [0] * 8)
    fcntl.ioctl(dev.fd, 0x8008451B, buf)  # EVIOCGSW(8)
    bits = int.from_bytes(buf.tobytes(), "little")
    return bool((bits >> e.SW_TABLET_MODE) & 1)


def keyboard_attached():
    """A keyboard with letters, on a bus it could be unplugged from."""
    for dev in _devices():
        keys = set(dev.capabilities().get(e.EV_KEY, []))
        if ALPHA_KEYS <= keys and dev.info.bustype in HOTPLUG_BUSES:
            return True
    return False


def session_env():
    """Enough of the user's session for a Wayland client to start.

    Discovered rather than inherited: this runs as root from a system unit, so
    none of it is in the environment.
    """
    sockets = sorted(glob.glob(f"{RUNTIME_DIR}/wayland-*"))
    sockets = [s for s in sockets if not s.endswith(".lock")]
    if not sockets:
        return None
    home = os.environ.get("USER_HOME")
    if not home:
        try:
            home = pwd.getpwuid(TARGET_UID).pw_dir
        except KeyError:
            home = "/"
    return {
        "XDG_RUNTIME_DIR": RUNTIME_DIR,
        "WAYLAND_DISPLAY": os.path.basename(sockets[0]),
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path={RUNTIME_DIR}/bus",
        "HOME": home,
        # The patched wvkbd from osk/ installs to ~/.local/bin, which is not on
        # root's PATH and is not inherited here.
        "PATH": f"{home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
    }


def osk_binary():
    return shlex.split(OSK_COMMAND)[0].rsplit("/", 1)[-1]


def osk_running():
    """True only for a live process. `pgrep` alone also matches zombies."""
    r = subprocess.run(["pgrep", "-u", str(TARGET_UID), "-x", osk_binary()],
                       capture_output=True, text=True)
    for pid in r.stdout.split():
        try:
            with open(f"/proc/{pid}/stat") as fh:
                if fh.read().rsplit(") ", 1)[1].split()[0] != "Z":
                    return True
        except OSError:
            continue
    return False


def start_osk():
    env = session_env()
    if env is None:
        log("no wayland session yet; will retry on the next event")
        return
    subprocess.Popen(
        ["setpriv", "--reuid", str(TARGET_UID), "--regid", str(TARGET_GID),
         "--init-groups", "--", "env", *[f"{k}={v}" for k, v in env.items()],
         *shlex.split(OSK_COMMAND)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )


def stop_osk():
    subprocess.run(["pkill", "-u", str(TARGET_UID), "-x", osk_binary()])


def apply(switch):
    tablet = read_tablet_mode(switch) if switch else False
    kbd = keyboard_attached()
    want = tablet or not kbd
    if want and not osk_running():
        log(f"keyboard unusable (tablet={tablet}, keyboard_attached={kbd}) -- starting {osk_binary()}")
        start_osk()
    elif not want and osk_running():
        log(f"keyboard usable again (tablet={tablet}, keyboard_attached={kbd}) -- stopping {osk_binary()}")
        stop_osk()


def inotify_devinput():
    """Watch /dev/input so attach and detach need no udev dependency."""
    libc = CDLL("libc.so.6", use_errno=True)
    libc.inotify_init1.restype = c_int
    libc.inotify_add_watch.argtypes = [c_int, c_char_p, c_uint32]
    fd = libc.inotify_init1(0o4000)  # IN_NONBLOCK
    if fd < 0:
        raise OSError(get_errno(), "inotify_init1")
    IN_CREATE, IN_DELETE, IN_ATTRIB = 0x100, 0x200, 0x4
    if libc.inotify_add_watch(fd, b"/dev/input", IN_CREATE | IN_DELETE | IN_ATTRIB) < 0:
        raise OSError(get_errno(), "inotify_add_watch")
    return fd


def main():
    # Let the kernel reap the keyboard process when it exits. Without this the
    # daemon leaves a zombie behind, and pgrep matches zombies -- so osk_running()
    # would report the keyboard as running forever and never start it again.
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)

    ino = inotify_devinput()
    switch = find_tablet_switch()
    log(f"tablet switch: {switch.name if switch else 'none (treating as no cover)'}")
    apply(switch)

    while True:
        fds = [ino] + ([switch.fd] if switch else [])
        try:
            ready, _, _ = select.select(fds, [], [], None)
        except (OSError, ValueError):
            switch = find_tablet_switch()
            continue

        if ino in ready:
            os.read(ino, 4096)
            # A device appeared or vanished: the switch itself may be gone on
            # models where it lives in the cover.
            new = find_tablet_switch()
            if (new is None) != (switch is None) or (
                new and switch and new.path != switch.path
            ):
                log(f"tablet switch now: {new.name if new else 'none'}")
                switch = new

        if switch and switch.fd in ready:
            try:
                for _ in switch.read():
                    pass
            except OSError:
                switch = find_tablet_switch()

        apply(switch)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
