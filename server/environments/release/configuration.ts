/** Application/archive format values shared by build and admission. */
export const APP_FORMAT = 'panels-app-v1' as const;
export const APP_ARCHIVE_NAME = 'panels-app.tar.gz';
export const PUBLICATION_RECEIPT_NAME = 'publication.json';
export const RUNTIME_CONTRACT_PATH = 'runtime/contract.json';
export const RUNTIME_INVENTORY_PATH = 'runtime/inventory.json';
export const FILE_CHUNK_BYTES = 1024 * 1024;
export const ARCHIVE_GZIP_LEVEL = 1;
export const NODE_RUNTIME_EXECUTABLES = ['bin/panels', 'bin/panels-launcher'] as const;
export const NODE_RUNTIME_FILES = [
  'runtime/installer-files.json',
  'server/lifecycle/foreground_main.ts',
  'server/lifecycle/managed_main.ts',
  'server/lifecycle/application.ts',
  'server/lifecycle/stop_main.ts',
  'server/conversation/usage/usageWorker.ts',
  'src/planner/conversation/backends/hermes_acp_extension.py',
  'src/planner/conversation/backends/hermes_model_catalog.py',
  'server/environments/release/launcher_main.ts',
  'server/environments/release/archive_unpack_main.ts',
  'server/environments/updates/interruption_capture_main.ts',
  'server/environments/updates/restore_main.ts',
  'server/environments/updates/installer/main.ts',
  'server/package.json',
  'server/package-lock.json',
  'server/node_modules/fastify/package.json',
  'server/node_modules/koffi/package.json',
  'server/node_modules/better-sqlite3/package.json',
  'server/node_modules/@anthropic-ai/claude-agent-sdk/package.json',
] as const;
export const SHARED_RUNTIME_FILES = [
  'web/dist/index.html',
  'agent_backends/node_modules/@anthropic-ai/claude-code/package.json',
  'agent_backends/node_modules/@openai/codex/package.json',
] as const;

/** Finite archive budgets. Disk reserve is checked before each spool batch. */
export const ARCHIVE_MAX_COMPRESSED_BYTES = 4 * 1024 ** 3;
export const ARCHIVE_MAX_EXPANDED_BYTES = 8 * 1024 ** 3;
export const ARCHIVE_MAX_MEMBER_BYTES = 4 * 1024 ** 3;
export const ARCHIVE_MAX_HEADERS = 250_000;
export const ARCHIVE_MAX_METADATA_ENTRY_BYTES = 1024 ** 2;
export const ARCHIVE_MAX_METADATA_BYTES = 64 * 1024 ** 2;
export const ARCHIVE_MAX_DEPTH = 64;
export const ARCHIVE_MAX_END_PADDING_BYTES = 20 * 1024;
export const ARCHIVE_SPACE_RESERVE_BYTES = 1024 ** 3;
export const ARCHIVE_SPOOL_BATCH_BYTES = 1024 ** 2;
export const ARCHIVE_STREAM_CHUNK_BYTES = 64 * 1024;
export const ARCHIVE_MAX_MESSAGE_BYTES = 64 * 1024;
