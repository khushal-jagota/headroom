#!/bin/bash
cd /home/vps/projects/panels/repository-worktrees/t_vv58wzxx-scroll
PLAN_DB_PATH=$PWD/data/probe/planner.db PLAN_PORT=8831 PLAN_DISPATCH_ENABLED=false \
PLAN_DISPATCHER_LOCK_PATH=$PWD/data/probe/dispatcher.lock PLAN_LOGS_DIR=$PWD/data/probe/logs \
PLAN_BACKUP_DIR=$PWD/data/probe/backups PLAN_SERVER_CONTROL_SOCKET=$PWD/data/probe/control.sock \
PLAN_APP_ROOT=$PWD nohup .venv/bin/python -m planner serve > data/probe/server.log 2>&1 &
echo $! > data/probe/server.pid
