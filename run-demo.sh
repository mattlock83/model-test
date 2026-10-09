#!/bin/bash
# Launch the macOS demo in a dedicated, visible Chrome profile.
set -euo pipefail

demo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$demo_root"

if ! command -v uv >/dev/null 2>&1; then
  echo "Install uv first: https://docs.astral.sh/uv/getting-started/installation/" >&2
  exit 2
fi

uv sync --frozen

# Help works without Chrome or an API key.
for argument in "$@"; do
  if [[ "$argument" == "--help" || "$argument" == "-h" ]]; then
    exec uv run --no-sync model-test demo --help
  fi
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This launcher is for macOS. Use: uv run model-test demo --headed" >&2
  exit 2
fi

# Read dotenv as data; never source it as shell code or print credentials.
uv run --no-sync python - <<'PY'
import os
import sys
from dotenv import load_dotenv

load_dotenv(".env")
if not os.environ.get("TYPESAFE_API_KEY"):
    sys.exit("Add TYPESAFE_API_KEY to .env, then rerun ./run-demo.sh")
PY

if uv run --no-sync python - <<'PY'
import os
import shutil
import sys
from dotenv import load_dotenv

load_dotenv(".env")
binary = os.environ.get("GRAPHWALKER_BIN")
if binary:
    if not shutil.which(binary):
        sys.exit("GRAPHWALKER_BIN does not name an executable GraphWalker CLI")
    sys.exit(0)
sys.exit(0 if os.access(".tools/graphwalker-rs/target/release/graphwalker", os.X_OK) else 3)
PY
then
  :
else
  setup_status=$?
  if [[ "$setup_status" != "3" ]]; then
    exit "$setup_status"
  fi
  echo "Building GraphWalker for the first run..."
  uv run --no-sync model-test setup
fi

chrome_port="${MODEL_TEST_CHROME_PORT:-9222}"
if ! [[ "$chrome_port" =~ ^[0-9]{1,5}$ ]] || (( 10#$chrome_port < 1024 || 10#$chrome_port > 65535 )); then
  echo "MODEL_TEST_CHROME_PORT must be an integer from 1024 through 65535" >&2
  exit 2
fi
chrome_port=$((10#$chrome_port))
chrome_profile="$demo_root/.tools/chrome-profile-$chrome_port"
mkdir -p "$chrome_profile"

echo "Opening Chrome for the demo..."
if ! open -na "Google Chrome" --args \
  --user-data-dir="$chrome_profile" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port="$chrome_port" \
  --no-first-run --no-default-browser-check; then
  echo "Could not open Google Chrome. Install Chrome and rerun this script." >&2
  exit 2
fi

export BU_NAME="model-test-$chrome_port"
export BU_CDP_URL="http://127.0.0.1:$chrome_port"

echo "Waiting for Chrome's debugging connection..."
uv run --no-sync python - <<'PY'
import json
import os
import sys
import time
import urllib.request

deadline = time.monotonic() + 20
url = os.environ["BU_CDP_URL"] + "/json/version"
# A local Chrome connection must not pass through an HTTP proxy.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
while time.monotonic() < deadline:
    try:
        with opener.open(url, timeout=1) as response:
            info = json.load(response)
        if isinstance(info, dict) and info.get("webSocketDebuggerUrl"):
            break
    except (OSError, ValueError):
        pass
    time.sleep(0.2)
else:
    sys.exit("Chrome was not ready after 20 seconds. Close the demo Chrome window and retry, "
             "or choose another port with MODEL_TEST_CHROME_PORT=9333 ./run-demo.sh")
PY

echo "Starting the demo. Each test tab will come to the front."
exec uv run --no-sync model-test demo --headed "$@"
