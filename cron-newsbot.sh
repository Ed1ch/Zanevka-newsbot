#!/bin/sh
# Use the same account/environment as the API; override only the interpreter.
NEWSBOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd) || exit 1
[ ! -e "$NEWSBOT_DIR/flags/.newsbot.disabled" ] || exit 0
[ ! -L "$NEWSBOT_DIR/flags/.newsbot.disabled" ] || exit 0
cd "$NEWSBOT_DIR" || exit 1
exec "${NEWSBOT_PYTHON:-$NEWSBOT_DIR/.venv/bin/python}" -u "$NEWSBOT_DIR/prelaunch.py"
