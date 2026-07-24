#!/usr/bin/env sh
set -eu

# Render/install input only. Review and run manually on the target Linux host.
groupadd --system panels-deploy
useradd --system --gid panels-deploy --home-dir /var/lib/panels/deploy panels-deploy
useradd --system --home-dir /var/lib/panels/live panels-live
useradd --system --home-dir /var/lib/panels/nonproduction panels-worker

install -d -m 0750 -o panels-live -g panels-live /var/lib/panels/environments/live
install -d -m 0750 -o panels-worker -g panels-worker /var/lib/panels/environments/staging
install -d -m 0775 -o root -g panels-deploy /opt/panels
install -d -m 0755 -o panels-deploy -g panels-deploy /opt/panels/releases
install -d -m 0750 -o panels-deploy -g panels-deploy /var/lib/panels/deployments
install -d -m 0750 -o panels-deploy -g panels-deploy /run/panels
install -d -m 0750 -o panels-worker -g panels-worker /opt/panels/staging
install -d -m 0755 -o root -g root /opt/panels/environment-manager
install -d -m 0755 -o root -g root /etc/panels/environments
install -m 0640 -o panels-live -g panels-live /dev/null /etc/panels/environments/live.env
install -m 0640 -o panels-live -g panels-live /dev/null /etc/panels/environments/maintenance.env
install -m 0640 -o panels-worker -g panels-worker /dev/null /etc/panels/environments/staging.env

chown -R panels-live:panels-live /var/lib/panels/environments/live
chown -R panels-worker:panels-worker /var/lib/panels/environments/staging
chown root:panels-deploy /opt/panels
chown -R panels-deploy:panels-deploy /opt/panels/releases
if [ -e /opt/panels/current ] || [ -L /opt/panels/current ]; then
  chown -h panels-deploy:panels-deploy /opt/panels/current
fi
chown panels-deploy:panels-deploy /var/lib/panels/deployments /run/panels
chown -R panels-worker:panels-worker /opt/panels/staging
