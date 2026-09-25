#!/bin/sh
# Observation only: a live process or a captured frame does not prove page display.
set -u
mode=${1:-default}
case "$mode" in
 default) flags='' ;;
 no-sandbox) flags='--no-sandbox' ;;
 *) echo "unsupported diagnostic mode: $mode"; exit 2 ;;
esac
s= w= b=
stop_process() {
 name=$1; pid=$2
 [ -n "$pid" ] || return 0
 forced="/tmp/m3-forced-$name-$pid"
 rm -f "$forced"
 if kill -0 "$pid" 2>/dev/null; then
  kill -TERM "$pid"
  (sleep 5; if kill -0 "$pid" 2>/dev/null; then
   : >"$forced"
   echo "FORCED_KILL_REQUESTED $name $pid"
   kill -KILL "$pid"
  fi) &
  guard=$!
  wait "$pid"; rc=$?
  kill "$guard" 2>/dev/null || true
  wait "$guard" 2>/dev/null || true
 else
  wait "$pid"; rc=$?
 fi
 echo "PROCESS_EXIT $name $pid $rc"
 [ ! -f "$forced" ] || return 1
 case "$rc" in 0|143) return 0 ;; *) return 1 ;; esac
}
export_logs() {
 for f in seat weston session browser; do
  echo "RAW_LOG_BEGIN $f"
  cat "/tmp/m3-$f.log" 2>/dev/null || true
  echo "RAW_LOG_END $f"
 done
}
cleanup() {
 result=$?
 trap - EXIT
 # Export before wait: a kernel exit regression must not hide the browser failure.
 export_logs
 stop_process chromium "$b" || result=1
 stop_process weston "$w" || result=1
 stop_process seatd "$s" || result=1
 echo POST_SHUTDOWN_LOGS
 export_logs
 exit "$result"
}
trap cleanup EXIT
/usr/local/bin/wayland-prepare || exit $?
sha256sum /opt/ict-testpages/*.html || exit $?
echo "M3_MODE $mode SEATD_VTBOUND=${SEATD_VTBOUND:-0}"
SEATD_VTBOUND=${SEATD_VTBOUND:-0} /usr/local/bin/wayland-seat >/tmp/m3-seat.log 2>&1 &
s=$!
sleep 1
su -s /bin/sh kiosk -c 'exec /usr/local/bin/wayland-session --log=/tmp/m3-weston.log --continue-without-input --drm-device=card0' >/tmp/m3-session.log 2>&1 &
w=$!
sleep 4
kill -0 "$w" || exit 3
su -s /bin/sh kiosk -c "exec env WAYLAND_DEBUG=1 /usr/local/bin/wayland-browser --enable-logging=stderr $flags" >/tmp/m3-browser.log 2>&1 &
b=$!
echo "BROWSER_LAUNCH_PID $b"
i=1
while [ "$i" -le 8 ]; do
 sleep 5
 printf '\n__ICT_FRAME_chromium-%s__\n' "$i"
 echo "OBSERVATION_BEGIN $i"
 for d in /proc/[0-9]*; do
  [ -r "$d/cmdline" ] || continue
  echo "PROC_CMDLINE ${d##*/}"
  tr '\000' ' ' <"$d/cmdline"; echo
 done
 echo "BROWSER_LOG_TAIL $i"
 tail -n 12 /tmp/m3-browser.log
 echo "OBSERVATION_END $i"
 if ! kill -0 "$b" 2>/dev/null; then
  echo BROWSER_EARLY_EXIT
  exit 4
 fi
 i=$((i+1))
done
echo CHROMIUM_OBSERVATION_COMPLETED
# The host must inspect original monitor pixels and raw protocol before acceptance.
