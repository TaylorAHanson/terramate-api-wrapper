#!/usr/bin/env bash
# Pushes fixtures/terraform-fixture-repo/'s current content to the standalone
# GitHub fixture repo (terramate-api-wrapper#22, Seam 3), creating it first if
# it doesn't exist yet. This directory is the source of truth — always edit
# here and re-run this script, rather than editing the pushed repo directly.
set -euo pipefail

FIXTURE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/terraform-fixture-repo" && pwd)"
REPO="${SEAM3_FIXTURE_REPO:-TaylorAHanson/terramate-fixture-repo}"

if ! gh repo view "$REPO" >/dev/null 2>&1; then
  echo "Creating $REPO..."
  gh repo create "$REPO" --private --description "Throwaway Terraform fixture repo for terramate-api-wrapper#22 (Seam 3)"
fi

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

cp -R "$FIXTURE_DIR"/. "$WORKDIR"/
cd "$WORKDIR"
git init -q -b main
git add -A
git commit -q -m "Sync fixture repo content from terramate-api-wrapper (#22)"
git remote add origin "https://github.com/$REPO.git"
git push -f origin main

echo "Pushed to https://github.com/$REPO"
