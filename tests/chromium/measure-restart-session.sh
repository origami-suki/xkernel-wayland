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

profile=/home/kiosk/.config/chromium-wayland
profile_snapshot() {
 stage=$1
 { echo "PROFILE_PATH $profile"; if [ -d "$profile" ]; then ls -lanR "$profile"; else echo PROFILE_ABSENT; fi; } >"/tmp/restart-profile-$stage.log"
}
chromium_pids() {
 for d in /proc/[0-9]*; do
  [ -r "$d/cmdline" ] || continue
  cmd=$(tr '\000' ' ' <"$d/cmdline" 2>/dev/null) || continue
  case "$cmd" in
   *'/usr/lib/chromium/chromium'*|*'/usr/bin/chromium'*|*'chrome_crashpad'*)
    echo "${d##*/} $cmd" ;;
  esac
 done
}
export_logs() {
 for f in seat weston session browser; do
  echo "RAW_LOG_BEGIN $f"; cat "/tmp/m3-$f.log" 2>/dev/null || true; echo "RAW_LOG_END $f"
 done
 for f in /tmp/restart-*.log; do
  [ -f "$f" ] || continue
  echo "RESTART_LOG_BEGIN $f"; cat "$f"; echo "RESTART_LOG_END $f"
 done
}
profile_snapshot initial
chromium_pids >/tmp/restart-before-first.log
[ ! -s /tmp/restart-before-first.log ] || { echo UNEXPECTED_OLD_CHROMIUM; exit 20; }
printf '\n__ICT_CAPTURE_START__\n'
su -s /bin/sh kiosk -c "exec env WAYLAND_DEBUG=1 /usr/local/bin/wayland-browser --enable-logging=stderr --ipc-connection-timeout=120 $flags" >/tmp/m3-browser.log 2>&1 &
b=$!
echo "FIRST_BROWSER_PID $b"
sleep 150
printf '\n__ICT_CAPTURE_FIRST_END__\n'
kill -0 "$b" || { echo FIRST_BROWSER_EARLY_EXIT; exit 21; }
chromium_pids >/tmp/restart-before-exit.log
tracked=$(awk '{print $1}' /tmp/restart-before-exit.log)
[ -n "$tracked" ] || { echo NO_TRACKED_CHROMIUM; exit 22; }
echo "FIRST_BROWSER_EXIT_REQUEST $b"
kill -TERM "$b"
(sleep 30; if kill -0 "$b" 2>/dev/null; then : >/tmp/restart-forced; kill -KILL "$b"; fi) &
guard=$!
wait "$b"; browser_rc=$?
kill "$guard" 2>/dev/null || true
wait "$guard" 2>/dev/null || true
old_browser=$b
b=
echo "FIRST_BROWSER_WAIT $old_browser $browser_rc"
[ ! -e /tmp/restart-forced ] || { echo FIRST_BROWSER_FORCED_EXIT; exit 23; }
case "$browser_rc" in 0|143) ;; *) exit 24 ;; esac
# Browser termination does not necessarily terminate crashpad/zygote helpers.
# Terminate only PIDs captured from this browser tree, before timing relaunch.
for pid in $tracked; do
 if kill -0 "$pid" 2>/dev/null; then
  echo "PREVIOUS_CHILD_TERM $pid"
  kill -TERM "$pid" 2>/dev/null || true
 fi
done
i=0
while :; do
 remaining=
 for pid in $tracked; do
  if [ -d "/proc/$pid" ] || kill -0 "$pid" 2>/dev/null; then remaining="$remaining $pid"; fi
 done
 [ -n "$remaining" ] || break
 echo "WAIT_PREVIOUS_PIDS $remaining"
 i=$((i+1))
 if [ "$i" -eq 10 ]; then
  for pid in $remaining; do
   echo "PREVIOUS_CHILD_KILL $pid"
   cat "/proc/$pid/stat" >>/tmp/restart-residual-stat.log 2>/dev/null || true
   kill -KILL "$pid" 2>/dev/null || true
  done
 fi
 [ "$i" -lt 40 ] || { echo PREVIOUS_PIDS_STILL_EXIST; exit 25; }
 sleep 1
done
chromium_pids >/tmp/restart-after-exit.log
[ ! -s /tmp/restart-after-exit.log ] || { echo CHROMIUM_REMAINS; exit 26; }
echo "ALL_PREVIOUS_CHROMIUM_EXITED $tracked"
profile_snapshot after-first
mv /tmp/m3-browser.log /tmp/restart-browser-first.log
kill -0 "$w" || exit 27
kill -0 "$s" || exit 28
echo "SAME_WESTON_PID $w SAME_SEATD_PID $s SAME_PROFILE $profile"
printf '\n__ICT_CAPTURE_RELAUNCH__\n'
su -s /bin/sh kiosk -c "exec env WAYLAND_DEBUG=1 /usr/local/bin/wayland-browser --enable-logging=stderr --ipc-connection-timeout=120 $flags" >/tmp/m3-browser.log 2>&1 &
b=$!
echo "SECOND_BROWSER_PID $b"
[ "$b" != "$old_browser" ] || exit 29
sleep 150
printf '\n__ICT_CAPTURE_SECOND_END__\n__ICT_CAPTURE_STOP__\n'
kill -0 "$b" || { echo SECOND_BROWSER_EARLY_EXIT; exit 30; }
profile_snapshot after-second
chromium_pids >/tmp/restart-second-processes.log
echo RESTART_COMPARISON_COMPLETED
