status=0
su -s /bin/sh kiosk -c /opt/ict-tests/chromium/dupfd || status=1
su -s /bin/sh kiosk -c /opt/ict-tests/chromium/dupfd-static || status=1
exit "$status"
