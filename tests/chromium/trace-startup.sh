#!/bin/sh
# Diagnostic tracing adds overhead; these runs are not performance samples.
set -eu
awk '
/^su -s \/bin\/sh kiosk -c .*wayland-browser/ {
 print "printf \"\\n__ICT_TRACE_BROWSER_LAUNCH__\\n\""
 sub(/--enable-logging=stderr /, "--enable-logging=stderr --ipc-connection-timeout=120 --trace-startup=toplevel,startup,base,browser,content,loading,navigation,renderer_host,renderer,blink,cc,viz,gpu,mojom,ipc,sequence_manager --trace-startup-duration=120 --trace-startup-format=proto --trace-startup-file=/tmp/ict-startup.pftrace --trace-startup-record-mode=record-until-full --default-trace-buffer-size-limit-in-kb=65536 ")
}
/^i=1$/ {
 print "i=0; while [ \"$i\" -lt 5 ]; do sleep 30; i=$((i+1)); printf \"\\n__ICT_FRAME_trace-%s__\\n\" \"$i\"; done"
 print "/opt/ict-tests/chromium/trace-export.sh"
 print "exit $?"
 exit
}
{ print }
' /opt/ict-tests/chromium/session.sh >/tmp/ict-trace-session.sh
sh -n /tmp/ict-trace-session.sh
exec sh /tmp/ict-trace-session.sh no-sandbox
