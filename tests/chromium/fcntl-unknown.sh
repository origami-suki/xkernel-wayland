status=0
su -s /bin/sh kiosk -c /opt/ict-tests/chromium/fcntl-unknown || status=1
su -s /bin/sh kiosk -c /opt/ict-tests/chromium/fcntl-unknown-static || status=1
exit "$status"
