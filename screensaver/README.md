# Screensaver touch helper

Two problems with Omarchy's screensaver on a touch-only setup:

1. The on-screen keyboard and virtual trackpad panels sit on Wayland's
   Overlay layer, above the screensaver's own window, so they'd otherwise
   float on top of it looking broken.
2. `omarchy-screensaver` only reads real keyboard/mouse input to know when
   to exit (a `read -n1 -t 1` loop internally) -- a touch on the screen
   can't reach that at all, so touching the screen while it's up would do
   nothing.

This daemon polls for the screensaver's window (`org.omarchy.screensaver`)
and, while it's active: hides the OSK/trackpad panels, and forwards any
touch to an injected `Escape` keypress via
[`../trackpad/`](../trackpad/)'s injector socket (so it needs that service
already running).

## Install

```
./install.sh
```

Needs [`../trackpad/`](../trackpad/) installed first. Installs as a
**user** systemd service.

> **Known issue:** running as a user service is wrong. This helper reads the
> touchscreen directly, but `/dev/input/event*` is `root:input` and logind
> hands the compositor its devices over D-Bus rather than through file
> permissions -- so as your user it sees no input devices at all and the
> touch-dismiss half never works. (The OSK/trackpad-hiding half is fine.) It
> needs converting to a system unit the way `trackpad-injector` and
> `two-finger-rightclick` already are. Until then it logs an explicit error
> saying so; check with
> `journalctl --user -u screensaver-touch-helper -n 20`.

## Customizing

The touchscreen is found by capability -- the input device exposing
`ABS_MT_SLOT`, which is what distinguishes it from the stylus and from the
raw uncalibrated HID nodes -- so there is normally nothing to set. Override it
only if more than one multitouch device is present:

```
systemctl --user edit screensaver-touch-helper.service
# [Service]
# Environment=TOUCH_DEVICE_NAME=Your Device Name Here
```
