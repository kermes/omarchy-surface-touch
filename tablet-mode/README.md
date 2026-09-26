# Tablet mode

Runs an on-screen keyboard only while the physical keyboard is unusable, and
stops it again when it is not.

Not installed by `--all`: it changes behaviour for anyone who relies on the
keyboard being available to toggle at any time.

```bash
./install.sh tablet-mode
```

## Two conditions, and why presence alone is not enough

The keyboard is unusable when either:

- the Type Cover is **detached**, or
- the Type Cover is **folded back** behind the screen. It is still attached and
  still enumerated, so a presence check misses the most common tablet posture.

Fold-back is reported as `SW_TABLET_MODE` on an `EV_SW` device. The predicate is
therefore *tablet mode **or** no usable keyboard*, not either alone.

## Nothing here matches on a device name

Where the switch lives differs by model, and the name is not a reliable handle:

| | Surface Go | Surface Pro 7+ |
|---|---|---|
| Device name | `Intel HID switches` | `Microsoft Surface Type Cover Tablet Mode Switch` |
| Provided by | the chassis | the Type Cover itself |
| On detach | switch persists | switch disappears with the cover |
| Name is unique? | yes | no: ten devices share it, one exposes `SW_TABLET_MODE` |

So on a Pro 7+ the two conditions collapse into one device's presence, while on
a Go they stay genuinely independent. A missing switch means "no cover", not
"not in tablet mode", which the predicate above already handles.

The switch is found by capability (`EV_SW` containing `SW_TABLET_MODE`), which
is correct on both.

Keyboard presence is also capability-based, with one wrinkle: an x86 machine
enumerates an `AT Translated Set 2 keyboard` on i8042 whether or not anything is
attached to it, so "has alphabetic keys" is always true. Presence therefore
requires alphabetic keys **on a hotpluggable bus** (USB or Bluetooth).

## Why a root system unit

It reads `/dev/input` directly, which is `root:input`, exactly as
`trackpad-injector` and `two-finger-rightclick` do. That avoids adding anyone to
the `input` group, needs no compositor dependency, and keeps per-model device
names out of the Hyprland config.

Attach and detach need no separate udev trigger: the devices appearing and
disappearing under `/dev/input` is the signal, watched with inotify.

The keyboard itself is started **as the session user** with `setpriv`, since it
must be the user's own Wayland client. The unit is deliberately not ordered
against the graphical session so it survives the compositor restarting; it
discovers the Wayland socket when it needs one and does nothing until one
exists.

## Choosing the keyboard

```bash
sudo systemctl edit omarchy-tablet-mode.service
```

```ini
[Service]
Environment="OSK_COMMAND=squeekboard"
ExecStopPost=
ExecStopPost=-/usr/bin/pkill -u <your uid> -x squeekboard
```

The default is the patched `wvkbd-deskintl` from [`../osk/`](../osk/). Anything
that stays in the foreground works.

> Quote the value. `Environment=` splits on whitespace, so an unquoted command
> with arguments is parsed as several assignments and the arguments are silently
> dropped.

**A note on what "running" means.** With `wvkbd --hidden` the keyboard is
launched but not shown; you still toggle it. With `squeekboard` it appears by
itself when a text field takes focus, so folding the cover while looking at a
window with no text field shows nothing until you tap one. Neither keyboard pops
up merely because the cover moved.

## Verify

```bash
systemctl status omarchy-tablet-mode.service
journalctl -u omarchy-tablet-mode.service -f
```

Then fold the cover, unfold it, and detach it. Expect one line per transition:

```
keyboard unusable (tablet=True,  keyboard_attached=True)  -- starting squeekboard
keyboard usable again (tablet=False, keyboard_attached=True) -- stopping squeekboard
keyboard unusable (tablet=False, keyboard_attached=False) -- starting squeekboard
```

The third line is a detach on a Surface Go: the chassis switch still reads
`False` while the keyboard is gone, which is why the predicate checks both.

**Fold twice.** A single cycle passing is not enough to prove the process is
being reaped correctly: `pgrep` matches zombies, so a daemon that leaks one
reports the keyboard as permanently running and silently stops starting it.
