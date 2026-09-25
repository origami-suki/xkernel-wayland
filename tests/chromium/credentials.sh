#!/bin/sh
set -e
su -s /bin/sh kiosk -c /opt/ict-tests/chromium/unix-credentials
/opt/ict-tests/chromium/unix-credentials mixed-ids
