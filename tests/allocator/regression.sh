#!/bin/sh
set -e
/opt/ict-tests/allocator-pressure
for n in proc-task proc-task-static scheduler-query scheduler-query-static unix-credentials cache-sync cache-sync-static fcntl-unknown fcntl-unknown-static dupfd dupfd-static; do
 su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/$n"
done
/opt/ict-tests/chromium/unix-credentials mixed-ids
for m in basic fork thread exec; do su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/no-new-privs $m"; done
for m in setid setid-control; do su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/no-new-privs $m /opt/ict-tests/chromium/no-new-privs-setid"; done
/opt/ict-tests/drm/drm-probe query
/opt/ict-tests/unix-rights/rights all
for m in pending poll epoll poll-thread epoll-thread; do /opt/ict-tests/drm/signalfd-probe "$m"; done
echo M5_BUDDY_M1_M2_REGRESSION_OK
