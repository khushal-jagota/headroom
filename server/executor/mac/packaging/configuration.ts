import { DRIVER_SOURCE_FILES } from '../../../environments/release/configuration.ts';
export const MAC_STATIC_FILES = [
  ...DRIVER_SOURCE_FILES,
  'server/worker_types/shipped_worker_types.json',
] as const;
export const MAC_REQUIRED_FILES = [
  'server/executor/mac/entrypoint.ts',
  'server/cli/main.ts',
  'server/executor/wire.generated.ts',
  'server/executor/wire.schema.json',
  'server/core/database/application_schema.json',
  ...MAC_STATIC_FILES,
] as const;
export const MAC_RUNTIME_CONSOLE = `#!/bin/sh
set -eu
case "$0" in */*) script_dir=\${0%/*};; *) script_dir=.;; esac
root=$(CDPATH= cd -- "$script_dir/.." && pwd -P)
unset NODE_OPTIONS NODE_PATH
exec "$root/node/bin/node" "$root/server/executor/mac/entrypoint.ts" "$@"
`;
