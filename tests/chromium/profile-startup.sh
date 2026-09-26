#!/bin/sh
# The host sends a literal first-frame line only after monitor pixel matching.
set -eu
export ICT_PROFILE_MODE=${1:-on}
case "$ICT_PROFILE_MODE" in on|off) ;; *) exit 2 ;; esac
awk '
/^su -s \/bin\/sh kiosk -c .*wayland-browser/ {
 print "[ \"$ICT_PROFILE_MODE\" = off ] || echo start >/proc/syscall_profile"
 print "printf \"\\n__ICT_CAPTURE_START__\\n\""
 sub(/--enable-logging=stderr /, "--enable-logging=stderr --ipc-connection-timeout=120 ")
}
/^i=1$/ {
 print "printf \"\\n__ICT_PROFILE_WAIT_FRAME__\\n\""
 print "IFS= read -r answer </dev/tty || exit 40"
 print "[ \"$answer\" = first-frame ] || { echo PROFILE_BAD_HANDSHAKE; exit 41; }"
 print "if [ \"$ICT_PROFILE_MODE\" = on ]; then"
 print " echo stop >/proc/syscall_profile || exit 42"
 print " cat /proc/syscall_profile >/tmp/profile-startup.csv || exit 43"
 print "fi"
 print "printf \"\\n__ICT_PROFILE_STARTUP_STOPPED__\\n\""
 print "for d in /proc/[0-9]*; do [ -r \"$d/cmdline\" ] || continue; printf \"PID %s \" \"${d##*/}\"; tr \"\\\\000\" \" \" <\"$d/cmdline\"; echo; done >/tmp/profile-processes.txt"
 print "[ \"$ICT_PROFILE_MODE\" = off ] || echo start >/proc/syscall_profile"
 print "printf \"\\n__ICT_PROFILE_STEADY_START__\\n\""
 print "sleep 30"
 print "if [ \"$ICT_PROFILE_MODE\" = on ]; then"
 print " echo stop >/proc/syscall_profile || exit 44"
 print " cat /proc/syscall_profile >/tmp/profile-steady.csv || exit 45"
 print " for stage in startup steady; do echo PROFILE_DATA_BEGIN_$stage; cat /tmp/profile-$stage.csv; echo PROFILE_DATA_END_$stage; done"
 print " sha256sum /tmp/profile-startup.csv /tmp/profile-steady.csv"
 print "fi"
 print "cat /tmp/profile-processes.txt"
 print "printf \"\\n__ICT_CAPTURE_STOP__\\n\""
 print "kill -0 \"$b\" || exit 46"
 print "echo PROFILE_OBSERVATION_COMPLETED"
 exit
}
{ print }
' /opt/ict-tests/chromium/session.sh >/tmp/profile-session.sh
sh -n /tmp/profile-session.sh
exec sh /tmp/profile-session.sh no-sandbox
