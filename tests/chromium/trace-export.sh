#!/bin/sh
# Transfer after the observation window. The host verifies both payload hashes.
set -eu
trace=/tmp/ict-startup.pftrace
snapshot=/tmp/ict-startup-export.pftrace
i=0
previous=0
stable=0
while [ "$i" -lt 30 ]; do
 size=$(stat -c %s "$trace" 2>/dev/null || echo 0)
 if [ "$size" -gt 0 ] && [ "$size" = "$previous" ]; then
  stable=$((stable+1))
 else
  stable=0
 fi
 [ "$stable" -lt 3 ] || break
 previous=$size
 i=$((i+1))
 sleep 2
done
[ "$stable" -ge 3 ] || { echo ICT_TRACE_ERROR_not_stable; exit 5; }
[ "$size" -le 67108864 ] || { echo ICT_TRACE_ERROR_too_large; exit 6; }
# Stability is an observation, not proof of trace completeness. Perfetto stats
# and event coverage are checked separately on the host.
cp "$trace" "$snapshot"
gzip -c "$snapshot" >"$snapshot.gz"
size=$(stat -c %s "$snapshot")
digest=$(sha256sum "$snapshot"); digest=${digest%% *}
zsize=$(stat -c %s "$snapshot.gz")
zdigest=$(sha256sum "$snapshot.gz"); zdigest=${zdigest%% *}
printf '\n__ICT_TRACE_BEGIN__ %s %s %s %s\n' "$size" "$digest" "$zsize" "$zdigest"
base64 "$snapshot.gz" | sed 's/^/__ICT_TRACE_DATA__ /'
printf '__ICT_TRACE_END__\n'
