#!/bin/bash
# Sets up a short numeric PIN for unlocking the lock screen by touch,
# completely decoupled from your real account password (a compromised PIN
# never exposes your login password, and vice versa), plus a Quickshell lock
# plugin with an on-screen PIN pad / QWERTY keyboard so the lock screen is
# unlockable with no physical keyboard attached.
#
# Prerequisite: Omarchy's own lock plugin system must already be present
# (it ships by default). This clones it rather than replacing it wholesale --
# see README.md for why, and for what "clonedFrom" means here.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ../lib.sh

# Must run as the user who will unlock the screen, not under sudo: $USER, $HOME
# and the PIN file's username field are all taken from the invoking user, and
# under `sudo ./install.sh` they all become root's. The PIN file would then name
# the wrong user, pam_pwdfile would never match it, and auth would silently fall
# through to pam_unix -- the PIN would just appear to be rejected.
if [[ $EUID -eq 0 ]]; then
  die "Run this as your normal user, not with sudo -- it calls sudo itself where needed."
fi

require_cmd openssl
require_cmd pacman
require_cmd setfacl "Install with: pacman -S acl"

pacman -Qq libpam_pwdfile >/dev/null 2>&1 || die "libpam_pwdfile is required. Install with: yay -S libpam_pwdfile"

PIN_FILE=/etc/omarchy-lock-pin.pwd
PLUGIN_DIR="$HOME/.config/omarchy/plugins/touchlock"

if [[ -e $PIN_FILE ]]; then
  warn "$PIN_FILE already exists -- leaving it alone. Delete it first to set a new PIN."
else
  echo -n "Choose a numeric PIN, 4+ digits (not your account password): "
  read -rs pin
  echo
  # 4 is the floor, not the recommendation -- see README.md for what each extra
  # digit buys against offline cracking. Matching what Windows Hello allows:
  # the online path is rate-limited by pam_faillock either way, and forcing a
  # longer PIN on a touch keypad trades real usability for a threat that needs
  # code execution as you AND physical access to the locked device.
  [[ $pin =~ ^[0-9]{4,}$ ]] || die "PIN must be 4+ digits."
  # Read the PIN on stdin rather than passing it as an argument: a process's
  # command line is readable by every user on the machine through
  # /proc/<pid>/cmdline for as long as it runs, so `openssl passwd -6 "$pin"`
  # publishes the PIN for the duration of the hash.
  #
  # rounds=200000 rather than openssl's 5000-round default: the hash ends up
  # readable by your own user (see the ACL below), so offline cracking is the
  # real threat, and at 5000 rounds a short numeric PIN falls in well under a
  # minute on one core. pam_pwdfile calls crypt_r, so libxcrypt honours the
  # $6$rounds=N$ form.
  hash=$(printf '%s' "$pin" | openssl passwd -6 -salt "rounds=200000\$$(openssl rand -hex 8)" -stdin)
  unset pin
  # Create with the final mode in one step: `sudo tee` lands the hash at 0644
  # under root's umask, and under `set -e` any failure before a later chmod
  # would leave it world-readable permanently, since the next run sees the
  # file and skips it.
  printf '%s:%s\n' "$(id -un)" "$hash" | sudo install -m 600 /dev/stdin "$PIN_FILE"
  # Quickshell runs the lock screen's PAM conversation as the logged-in user,
  # so pam_pwdfile opens this file unprivileged. With 600 root:root it fails
  # with "couldn't open password file" and auth silently falls through to
  # pam_unix, leaving the PIN apparently rejected. pam_unix is unaffected
  # because it reads shadow via the setuid unix_chkpwd helper.
  #
  # Grant read to exactly one user via an ACL rather than widening the group:
  # a primary group is not always private (`useradd -g users` makes it the
  # shared `users` group, which exists on a stock install).
  sudo setfacl -m "u:$(id -un):r" "$PIN_FILE"
  info "Wrote $PIN_FILE"
fi

# The ACL above exists so pam_pwdfile, which runs as your user, can read this
# file; when it cannot, the only symptom is a correct PIN being rejected at the
# lock screen. Read it back once so that surfaces here instead. This also
# catches a file left by an older version of this script, which the
# "already exists" branch deliberately does not touch.
if [[ ! -r $PIN_FILE ]]; then
  warn "$PIN_FILE exists but is not readable by $(id -un)."
  warn "pam_pwdfile runs as your user, so the PIN would be silently rejected."
  die "Delete it and re-run to rewrite it correctly: sudo rm $PIN_FILE"
fi

sudo cp omarchy-lock-password.pam /etc/pam.d/omarchy-lock-password
info "Installed /etc/pam.d/omarchy-lock-password"

mkdir -p "$PLUGIN_DIR"
cp plugin/*.qml plugin/manifest.json "$PLUGIN_DIR/"
info "Installed lock plugin to $PLUGIN_DIR"

cat <<'EOF'

==> Enable the plugin through Omarchy's own plugin settings (Setup > Plugins,
    or wherever your Omarchy version surfaces `~/.config/omarchy/plugins/`),
    then lock your session to test: hyprctl dispatch exec 'omarchy-lock' (or
    however you normally trigger the lock screen).

Forgot your PIN? Your real account password still unlocks the session (the
password field in this plugin falls through to pam_unix -- see
omarchy-lock-password.pam). Delete /etc/omarchy-lock-pin.pwd and re-run this
script to set a new PIN.
EOF
