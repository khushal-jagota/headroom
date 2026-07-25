#!/usr/bin/env sh
set -eu

repository_url=${1:?usage: configure-deployment-runner.sh REPOSITORY_URL REGISTRATION_TOKEN}
registration_token=${2:?usage: configure-deployment-runner.sh REPOSITORY_URL REGISTRATION_TOKEN}
runner_root="$HOME/Coding/Panels/.github-runner"

exec "$runner_root/config.sh" \
  --unattended \
  --url "$repository_url" \
  --token "$registration_token" \
  --name panels-vps-deployment \
  --labels production,panels-deploy \
  --work _work \
  --replace
