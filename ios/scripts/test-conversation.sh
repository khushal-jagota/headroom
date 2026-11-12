#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")/../.."
exec ios/scripts/test-offline.sh
