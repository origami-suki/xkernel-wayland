#!/bin/sh
# Run both libc paths, retaining both failures on a pre-fix kernel.
failed=0
for name in scheduler-query scheduler-query-static; do
    su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/$name" || failed=1
done
[ "$failed" -eq 0 ] || exit 1
echo SCHEDULER_QUERIES_OK
