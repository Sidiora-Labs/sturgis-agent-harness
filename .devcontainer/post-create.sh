#!/usr/bin/env bash
set -euo pipefail

development_root=$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)
cd -- "$development_root"
make bootstrap
make browsers
printf '\nDevelopment tools are ready. Run make build, then make dev to start Sturgis.\n'
printf 'Hosted isolation and managed browser tools require namespace support from the outer host.\n'
