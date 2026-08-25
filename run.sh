#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec conda run -n hpc-sync python "$project_dir/main.py" "$@"
