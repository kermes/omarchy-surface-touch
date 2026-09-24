#!/bin/bash
# Installs a systemd-sleep hook that re-inits the Type Cover touchpad's
# multitouch mode on every resume from suspend. See README.md for the
# symptom this fixes and why it happens.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ../lib.sh

# Must stay identical to the glob in rebind-surface-touchpad.sh. When the two
# differ this check can pass while the hook never fires -- which is exactly what
# happened on models whose Type Cover reports a product id other than 09C0.
# (Left unquoted on purpose: quoting it would stop the shell expanding it.)
if ! ls /sys/bus/hid/drivers/hid-multitouch/0003:045E:*.* >/dev/null 2>&1; then
  warn "No Microsoft (045E) device is bound to hid-multitouch right now."
  warn "That is normal if the Type Cover is detached, or if the touchpad is"
  warn "working correctly -- the device only appears there while bound."
  bound=$(ls -1 /sys/bus/hid/drivers/hid-multitouch/ 2>/dev/null | grep -E '^[0-9A-Fa-f]{4}:' || true)
  if [ -n "$bound" ]; then
    warn "Currently bound to hid-multitouch:"
    printf '         %s\n' $bound >&2
  else
    warn "Nothing is bound to hid-multitouch at all."
  fi
fi

# -D: systemd ships /usr/lib/systemd/system-sleep/ but not the /etc one, so on
# a default Arch install this target directory does not exist yet.
sudo install -Dm 755 rebind-surface-touchpad.sh /etc/systemd/system-sleep/rebind-surface-touchpad.sh
info "Installed /etc/systemd/system-sleep/rebind-surface-touchpad.sh"
info "Takes effect on the next suspend/resume -- no service to enable or reload."
info "Test it immediately with: sudo /etc/systemd/system-sleep/rebind-surface-touchpad.sh post suspend"
info "Check it fired after a real resume with: journalctl -t rebind-surface-touchpad"
