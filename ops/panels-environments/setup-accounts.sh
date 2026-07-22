#!/usr/bin/env sh
set -eu

# Render/install input only. Review and run manually on the target Linux host.
useradd --system --home-dir /var/lib/panels/live panels-live
useradd --system --home-dir /var/lib/panels/nonproduction panels-worker

install -d -m 0750 -o panels-live -g panels-live /var/lib/panels/environments/live
install -d -m 0750 -o panels-worker -g panels-worker /var/lib/panels/environments/staging
install -d -m 0750 -o panels-live -g panels-live /opt/panels/live
install -d -m 0750 -o panels-worker -g panels-worker /opt/panels/staging
install -d -m 0755 -o root -g root /etc/panels/environments
install -m 0640 -o panels-live -g panels-live /dev/null /etc/panels/environments/live.env
install -m 0640 -o panels-worker -g panels-worker /dev/null /etc/panels/environments/staging.env

chown -R panels-live:panels-live /var/lib/panels/environments/live
chown -R panels-worker:panels-worker /var/lib/panels/environments/staging
chown -R panels-live:panels-live /opt/panels/live
chown -R panels-worker:panels-worker /opt/panels/staging
