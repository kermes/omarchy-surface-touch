#!/bin/bash
# Installs accelerometer-driven auto-rotate as a user systemd service.
# Panel geometry is detected from `hyprctl monitors -j` at startup, so this
# works on any model; see README.md for the ROTATE_* overrides if the
# detection picks the wrong output.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
source ../lib.sh

require_cmd monitor-sensor "Install with: sudo pacman -S iio-sensor-proxy"
require_cmd hyprctl
require_cmd jq "Install with: sudo pacman -S jq"

install_bin auto-rotate.sh
install_bin omarchy-toggle-rotation-lock
install_user_unit auto-rotate.service

systemctl --user daemon-reload
# `enable --now` starts the unit only if it is not already running, so on an
# upgrade it reports success while leaving the previous process and the old
# script loaded. Enable, then restart explicitly.
systemctl --user enable auto-rotate.service
systemctl --user restart auto-rotate.service

info "auto-rotate.service is running."
info "Panel geometry is detected at startup; override with ROTATE_MONITOR/ROTATE_MODE/ROTATE_POS/ROTATE_SCALE"
info "  via: systemctl --user edit auto-rotate.service   (see README.md)"
info "Lock/unlock rotation with: omarchy-toggle-rotation-lock -- bind a key to it in ~/.config/hypr/bindings.lua"
