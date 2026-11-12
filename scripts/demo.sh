#!/usr/bin/env sh
# One-command demo: creates a virtualenv if needed, installs the demo extra, runs the app.
#   ./scripts/demo.sh            # then open http://localhost:8000
set -eu
here="$(cd "$(dirname "$0")/.." && pwd)"
venv="${VALIDATE_VENV:-$here/.venv-demo}"
if [ ! -x "$venv/bin/python" ]; then
  python3 -m venv "$venv"
fi
"$venv/bin/pip" install -q -e "$here/backend[demo]"
cd "$here/backend"
exec "$venv/bin/python" -m app.demo "$@"
