#!/bin/sh
set -e
for mode in basic fork thread exec; do
 su -s /bin/sh kiosk -c "/opt/ict-tests/chromium/no-new-privs $mode"
done
su -s /bin/sh kiosk -c '/opt/ict-tests/chromium/no-new-privs setid /opt/ict-tests/chromium/no-new-privs-setid'
su -s /bin/sh kiosk -c '/opt/ict-tests/chromium/no-new-privs setid-control /opt/ict-tests/chromium/no-new-privs-setid'
echo NNP_PREREQUISITES_OK
