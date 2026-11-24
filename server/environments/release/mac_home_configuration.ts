import { MAC_STATIC_FILES } from '../../executor/mac/packaging/configuration.ts';
import { NODE_RUNTIME_FILES, SHARED_RUNTIME_FILES } from './configuration.ts';

export const MAC_HOME_MINIMUM_OS = '15.0';
export const MAC_HOME_SYSTEM_LIBRARIES = ['/usr/lib/', '/System/Library/'] as const;
export const MAC_HOME_NATIVE_TOOL_SECONDS = 30;
/** Shared browser policy stays at its source path; the server imports the same value. */
export const MAC_HOME_SERVER_WEB_FILES = [
  'web/src/lib/conversation/view/contracts.ts',
  'web/src/lib/conversation/attachmentPolicy.ts',
] as const;
export const MAC_HOME_REQUIRED_FILES = [
  ...NODE_RUNTIME_FILES,
  ...SHARED_RUNTIME_FILES,
  ...MAC_STATIC_FILES,
  'server/core/database/application_schema.json',
  'server/environments/release/migration_graph.json',
  'server/core/database/python_baseline/application_schema.json',
  'server/core/database/python_baseline/migration_graph.json',
  'server/installation/database_main.ts',
  ...MAC_HOME_SERVER_WEB_FILES,
  'server/worker_types/shipped_worker_types.json',
] as const;
export const MAC_HOME_PUBLICATION_METADATA = 'mac-home-build.json';
