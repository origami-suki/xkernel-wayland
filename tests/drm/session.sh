#!/bin/sh
# x-kernel M2 functional probe: serial-only seat, input acceptance is separate.
set -e
/usr/local/bin/wayland-prepare
SEATD_VTBOUND=0 /usr/local/bin/wayland-seat >/tmp/seat.log 2>&1 &
s=$!
sleep 1
su -s /bin/sh kiosk -c 'exec /usr/local/bin/wayland-session --log=/tmp/w.log --continue-without-input --drm-device=card0' >/tmp/s.log 2>&1 &
w=$!
sleep 4
cat /tmp/seat.log /tmp/w.log /tmp/s.log
kill -0 "$w"
su -s /bin/sh kiosk -c 'exec env WAYLAND_DEBUG=1 /usr/local/bin/wayland-client' >/tmp/c.log 2>&1 &
c=$!
sleep 2
kill -0 "$c"
printf '\n%s\n' '__ICT_FRAME_weston-1__'
sleep 3
printf '\n%s\n' '__ICT_FRAME_weston-2__'
sleep 3
kill -INT "$c"
wait "$c"
echo CLIENT_EXIT_OK
cat /tmp/c.log
kill -TERM "$w"
wait "$w"
kill -TERM "$s"
wait "$s"
cat /tmp/seat.log /tmp/w.log /tmp/s.log
echo WAYLAND_SESSION_OK
