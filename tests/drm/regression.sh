#!/bin/sh
set -e
test "$(cat /sys/class/drm/card0/dev)" = 226:0
test "$(stat -c '%t:%T' /dev/dri/card0)" = e2:0
test "$(stat -c '%a' /sys/class/drm/card0/dev)" = 444
test "$(readlink -f /sys/class/drm/card0)" = "$(readlink -f /sys/dev/char/226:0)"
/opt/ict-tests/drm/drm-probe query
/opt/ict-tests/unix-rights/rights all
for mode in pending poll epoll poll-thread epoll-thread; do /opt/ict-tests/drm/signalfd-probe "$mode"; done
/usr/lib/chromium/chromium --version
weston --version
seatd -v
sh -c 'echo NESTED_SHELL_OK'
echo M2_REGRESSION_OK
