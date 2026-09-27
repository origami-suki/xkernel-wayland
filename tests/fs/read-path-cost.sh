#!/bin/sh
set -eu
# Modes are ordered; only the first batch pass is the first probe read in guest.
# Neither mode-first-use nor mode-warm claims a cold filesystem cache.
exec sh /opt/ict-tests/read-path-cost-run.sh
