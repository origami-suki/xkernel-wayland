#!/bin/sh
# Keep the successful browser parameters; replace intrusive observations with
# one quiet wait while the host captures monitor frames every second.
set -eu
awk '
/^su -s \/bin\/sh kiosk -c .*wayland-browser/ {
 print "printf \"\\n__ICT_CAPTURE_START__\\n\""
 sub(/--enable-logging=stderr /, "--enable-logging=stderr --ipc-connection-timeout=120 ")
}
/^i=1$/ {
 print "sleep 150"
 print "printf \"\\n__ICT_CAPTURE_STOP__\\n\""
 print "echo CHROMIUM_TIMED_OBSERVATION_COMPLETED"
 exit
}
{ print }
' /opt/ict-tests/chromium/session.sh >/tmp/m3-measure-first-frame.sh
sh -n /tmp/m3-measure-first-frame.sh
chmod 755 /tmp/m3-measure-first-frame.sh
/tmp/m3-measure-first-frame.sh no-sandbox
