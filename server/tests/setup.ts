import { setFlagsFromString } from 'node:v8';
import { afterAll } from 'vitest';
import { clean_test_environment } from './clean_environment.ts';

// Node 24 WASM wrapper lifetime race: https://github.com/nodejs/node/issues/66366
// Set at runtime, as the entry points do. A V8 flag in execArgv makes Worker construction fail.
if (process.versions.node.startsWith('24.')) setFlagsFromString('--no-wasm-code-gc');

// Vitest isolates worker environments from the global setup process.
afterAll(clean_test_environment());
