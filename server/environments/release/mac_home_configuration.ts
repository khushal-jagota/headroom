import { MAC_STATIC_FILES } from '../../executor/mac/packaging/configuration.ts';
import { NODE_RUNTIME_FILES, SHARED_RUNTIME_FILES } from './configuration.ts';

export const MAC_HOME_MINIMUM_OS = '15.0';
export const MAC_HOME_SYSTEM_LIBRARIES = ['/usr/lib/', '/System/Library/'] as const;
export const MAC_HOME_NATIVE_TOOL_SECONDS = 30;
export const MAC_HOME_REQUIRED_FILES = [
  ...NODE_RUNTIME_FILES,
  ...SHARED_RUNTIME_FILES,
  ...MAC_STATIC_FILES,
  'server/core/database/application_schema.json',
  'server/environments/release/migration_graph.json',
  'server/skills/cli_commands.json',
  'server/installation/database_main.ts',
  'src/planner/worker_types/shipped_worker_types.json',
] as const;
export const MAC_HOME_PUBLICATION_METADATA = 'mac-home-build.json';
