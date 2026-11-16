#!/usr/bin/env node
/** The signed launcher and versioned runtimes enter the same ordinary CLI. */
import { main } from '../../cli/main.ts';
import { setFlagsFromString } from 'node:v8';
// Node 24 WASM wrapper lifetime race: https://github.com/nodejs/node/issues/66366
if (process.versions.node.startsWith('24.')) setFlagsFromString('--no-wasm-code-gc');
await main();
