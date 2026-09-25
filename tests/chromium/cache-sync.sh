#!/bin/sh
set -u
result=0
for binary in cache-sync cache-sync-static; do
 echo "CACHE_SYNC_BEGIN $binary"
 su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/$binary" || result=1
 echo "CACHE_SYNC_END $binary"
done
exit "$result"
