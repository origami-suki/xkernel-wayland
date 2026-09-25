#!/bin/sh
# Diagnostic logging only: keep the browser, original page and rendering flags.
set -eu
sed 's/--enable-logging=stderr /--enable-logging=stderr --v=1 /' \
 /opt/ict-tests/chromium/session.sh >/tmp/m3-observe-stages.sh
chmod 755 /tmp/m3-observe-stages.sh
/tmp/m3-observe-stages.sh no-sandbox
