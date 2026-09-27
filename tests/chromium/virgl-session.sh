#!/bin/sh
# Functional GPU observer only; no timing or software-baseline comparison.
set -eu
s= w=
cleanup() {
 result=$?
 trap - EXIT
 echo VIRGL_PRE_SHUTDOWN_LOGS
 for name in chromium weston session seat; do
  echo "VIRGL_RAW_LOG_BEGIN $name"
  cat "/tmp/m2-004-$name.log" 2>/dev/null || true
  echo "VIRGL_RAW_LOG_END $name"
 done
 stop_service weston "$w" || result=1
 stop_service seat "$s" || result=1
 echo VIRGL_POST_SHUTDOWN_LOGS
 for name in chromium weston session seat; do
  echo "VIRGL_RAW_LOG_BEGIN $name"
  cat "/tmp/m2-004-$name.log" 2>/dev/null || true
  echo "VIRGL_RAW_LOG_END $name"
 done
 exit "$result"
}
stop_service() {
 name=$1; pid=$2
 [ -n "$pid" ] || return 0
 forced="/tmp/m2-004-forced-$name-$pid"
 rm -f "$forced"
 kill -TERM "$pid" 2>/dev/null || true
 (sleep 30; if kill -0 "$pid" 2>/dev/null; then
  : >"$forced"
  echo "VIRGL_FORCED_KILL $name $pid"
  kill -KILL "$pid" 2>/dev/null || true
 fi) &
 guard=$!
 if wait "$pid"; then rc=0; else rc=$?; fi
 kill "$guard" 2>/dev/null || true
 wait "$guard" 2>/dev/null || true
 echo "VIRGL_PROCESS_EXIT $name $pid $rc"
 [ ! -f "$forced" ] || return 1
 case "$rc" in 0|143) return 0 ;; *) return 1 ;; esac
}
trap cleanup EXIT
wait_socket() {
 path=$1; pid=$2; attempts=0
 while [ ! -S "$path" ]; do
  kill -0 "$pid" || return 1
  attempts=$((attempts + 1))
  [ "$attempts" -le 90 ] || return 1
  sleep 1
 done
}
/usr/local/bin/wayland-prepare
sha256sum /opt/ict-testpages/*.html
SEATD_VTBOUND=0 /usr/local/bin/wayland-seat >/tmp/m2-004-seat.log 2>&1 &
s=$!
wait_socket /run/seatd.sock "$s"
su -s /bin/sh kiosk -c 'exec /usr/local/bin/wayland-session --log=/tmp/m2-004-weston.log --continue-without-input --drm-device=card0' >/tmp/m2-004-session.log 2>&1 &
w=$!
wait_socket /run/user/1000/wayland-0 "$w"
if su -s /bin/sh kiosk -c 'exec env XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 /opt/ict-tests/drm/gpu-observer /tmp/m2-004-chromium.log 18'; then
 rc=0
else
 rc=$?
fi
echo "VIRGL_PROCESS_EXIT gpu-observer-helper $rc"
[ "$rc" -eq 0 ] || exit "$rc"
echo VIRGL_SESSION_OBSERVATION_DONE
