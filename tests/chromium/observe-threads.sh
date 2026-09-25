#!/bin/sh
# Observation only. Thread names identify roles, not completed initialization.
# Keep browser options unchanged; extend the existing bounded session observer.
set -eu
sed 's/"$i" -le 8/"$i" -le 12/;s/^ sleep 5$/ sleep 10/
/echo "OBSERVATION_BEGIN $i"/a\
 for t in /proc/[0-9]*/task/[0-9]*; do [ -r "$t/comm" ] || continue; name=; IFS= read -r name <"$t/comm" || true; printf "THREAD_COMM %s %s\n" "$t" "$name"; done
' /opt/ict-tests/chromium/session.sh >/tmp/m3-observe-threads.sh
chmod 755 /tmp/m3-observe-threads.sh
/tmp/m3-observe-threads.sh no-sandbox
