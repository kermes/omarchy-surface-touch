# Touch-friendly lock screen (PIN + on-screen keyboard)

Your account password is probably too long/complex to comfortably type on
glass with no physical keyboard attached. This adds a short numeric PIN as
an *additional* way to unlock -- your real password still works too -- plus
an on-screen PIN pad / QWERTY keyboard on the lock screen itself, so it's
unlockable with no keyboard present at all.

## Why a separate PIN instead of just a shorter password

`/etc/omarchy-lock-pin.pwd` stores the PIN's hash completely separately from
your account password (`/etc/shadow`), via
[`pam_pwdfile`](https://github.com/tobiadam/pam_pwdfile). If the PIN file
ever leaked, it exposes only the PIN -- not your login password, not your
disk encryption passphrase, nothing else. And the PIN only works against the
*lock screen* PAM stack, not `login`, `sudo`, `su`, or SSH.

## How the PAM stack works

[`omarchy-lock-password.pam`](omarchy-lock-password.pam) tries the PIN
first, and falls through to your real password if the PIN doesn't match:

```
auth  [success=2 default=ignore]  pam_pwdfile.so pwdfile=/etc/omarchy-lock-pin.pwd
auth  [success=1 default=bad]     pam_unix.so try_first_pass nullok
```

So either your PIN or your real account password unlocks the screen --
whichever you type into the same field.

## The plugin

Omarchy's lock screen is a Quickshell plugin. This clones the default one
(`omarchy.lock`) and swaps in a `VirtualKeyboard.qml` + `Key.qml`
touch-typable keypad under the password field (see
[`plugin/VirtualKeyboard.qml`](plugin/VirtualKeyboard.qml)): numeric by
default, with an "ABC" key to switch to a full QWERTY layout for typing your
real password instead of a PIN. Typed characters go straight into the
password field via `insert()`/`remove()` -- nothing here is logged, stored,
or transmitted anywhere.

`Service.qml` is otherwise Omarchy's own stock lock-service boilerplate
(session lock lifecycle, fingerprint PAM, idle-blank timing, stranded-lock
recovery) -- included as-is because the plugin needs the whole thing to
load, but the touch-specific work is entirely in `VirtualKeyboard.qml` /
`Key.qml` / `LockView.qml`.

## Install

```
./install.sh
```

Needs `libpam_pwdfile` (`yay -S libpam_pwdfile`), `openssl` and `setfacl`
(`pacman -S acl`). Prompts for a PIN (4+ digits), hashes it with
`openssl passwd -6` at 200,000 rounds, and writes
`/etc/omarchy-lock-pin.pwd` as `username:hash`, mode 600 owned by
`root:root` with a POSIX ACL granting read to your user alone. Then
installs the PAM config and copies the plugin to
`~/.config/omarchy/plugins/touchlock/`.

### Choose a PIN you do not use anywhere else

Quickshell runs the lock screen's PAM conversation **as your user**, so
`pam_pwdfile` has to be able to read the hash unprivileged. That is what the
ACL grants, and it means any process running as you can read the hash and
attack it offline. Nothing about this design avoids that.

Rounds are set to 200,000 rather than `openssl`'s 5000-round default to make
that attack expensive, but a short numeric PIN is still a small search space:

| rounds | per guess | 4-digit | 6-digit |
|---|---|---|---|
| 5000 (openssl default) | 5.7 ms | 27 s | 95 min |
| 200,000 (used here) | 181 ms | 30 min | 50 h |

Measured on one CPU core; a GPU is orders of magnitude faster. Each extra
digit multiplies the work by ten, so the table is there to let you choose
rather than to push you somewhere: **the minimum is 4 digits**, the same as
Windows Hello allows, and 6 costs an attacker a hundred times more.

Two things bound how much this matters. `pam_faillock` allows 10 attempts per
120 seconds, so guessing *at the lock screen* takes roughly 33 hours to
exhaust a 4-digit space regardless of rounds. And the offline attack needs
code execution as your user to read the hash in the first place, plus physical
access to the locked device to use what it recovers.

Worth knowing where this differs from Windows Hello, which allows the same 4
digits: Hello keeps the PIN in the TPM with rate limiting in hardware, so
there is no hash to steal and no offline attack at all. Here the hash is
readable by your own user by design, because `pam_pwdfile` runs unprivileged.
Rounds are what stands in for the TPM, and they are a weaker substitute.

**Do not reuse a PIN you use for anything else** — it protects a lock screen,
not your account, and it is not stored with the same protections as your
account password.

You still need to **enable the plugin** through however your Omarchy version
surfaces plugin management (Setup > Plugins, or directly editing
`~/.config/omarchy/shell.json` if you know that format) -- this repo installs
the plugin's files but doesn't assume how your Omarchy version wires plugin
enablement.

## If a correct PIN is rejected

Two independent bugs used to produce this exact symptom, and fixing either
one alone still leaves it, so check both:

1. **The PIN file was unreadable** by the user running the PAM conversation,
   so `pam_pwdfile` was never consulted and auth fell through to `pam_unix`.
   `journalctl` shows `couldn't open password file` rather than
   `wrong password for user`. `install.sh` now reads the file back and fails
   loudly instead, but a file written by an older version is left untouched --
   delete it and re-run.
2. **The keypad's Enter key was off-screen** below the bottom edge on short
   displays (under roughly 731px logical height), so the PIN was entered but
   never submitted. Nothing is logged at all in that case, because nothing
   was ever sent.

Both are fixed; the distinction matters when debugging a fresh install on
unfamiliar hardware. The `journalctl` line tells you which one you have.

## Changing or removing your PIN

Delete `/etc/omarchy-lock-pin.pwd` and re-run `./install.sh` to set a new
one. Delete it and don't re-run anything to remove the PIN entirely (the
PAM stack falls through to your real password when the file is absent
-- `pam_pwdfile` fails open to `default=ignore`, not a hard error).
