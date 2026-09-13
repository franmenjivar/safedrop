#!/usr/bin/env bash
# Build the submission archive.
#
# Ships everything needed to run the project and reproduce the result, and
# nothing else: no virtualenv, no credentials, no git history, no scratch data.
#
#   ./scripts/package_submission.sh          -> safedrop-submission.zip
set -euo pipefail

cd "$(dirname "$0")/.."
OUT="safedrop-submission.zip"
rm -f "$OUT"

zip -r -q "$OUT" \
  app baseline evaluation scripts tests data config \
  README.md CHANGELOG.md REPRODUCE.md revision.md videoscript.md \
  requirements.txt pyproject.toml \
  Dockerfile docker-compose.yml .dockerignore .env.example .gitignore \
  results trajectories \
  -x '*/__pycache__/*' '*.pyc' '*/.DS_Store' '.DS_Store' \
     '*/.pytest_cache/*' 'trajectories/*.jsonl'

echo "$OUT"
unzip -l "$OUT" | tail -1
du -h "$OUT" | cut -f1 | xargs echo "size:"
