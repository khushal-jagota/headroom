// Compile and package outside the checkout. This script never publishes a release.
import { spawnSync } from 'node:child_process';
import { appendFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Arch, build, Platform } from 'electron-builder';

const desktop = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const require = createRequire(import.meta.url);
const [version, outputArgument, proofFeedArgument] = process.argv.slice(2);
if (!version || !/^\d+\.\d+\.\d+$/.test(version) || !outputArgument)
  throw new Error('Usage: build_signed.mjs VERSION OUTPUT [PRIVATE_PROOF_FEED_URL]');
const output = resolve(outputArgument);
let proofFeed;
if (proofFeedArgument) {
  const url = new URL(proofFeedArgument);
  if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash)
    throw new Error('The private proof feed must be an HTTP(S) directory without credentials, query, or fragment.');
  if (!url.pathname.endsWith('/')) url.pathname += '/';
  proofFeed = { provider: 'generic', url: url.href };
}
mkdirSync(output, { recursive: true });
const compiled = join(output, 'build');
// Supply one complete file. electron-builder's implicit merge combines fields from
// different providers, so a generic proof feed otherwise inherits GitHub fields.
const { load } = require('js-yaml');
const configuration = {
  ...load(readFileSync(join(desktop, 'electron-builder.yml'), 'utf8')),
  directories: { output: join(output, 'dist') },
  files: [{ from: compiled, to: 'build' }, 'package.json'],
  extraMetadata: { version, ...(proofFeed ? { panelsUpdateFeed: proofFeed } : {}) },
  ...(proofFeed ? { publish: [proofFeed] } : {}),
};
const configurationPath = join(output, 'electron-builder.json');
writeFileSync(configurationPath, JSON.stringify(configuration));
if (process.argv.includes('--validate-config-only')) {
  const { getConfig, validateConfiguration } = require('app-builder-lib/out/util/config/config');
  const { DebugLogger } = require('builder-util');
  const effective = await getConfig(desktop, configurationPath, null);
  await validateConfiguration(effective, new DebugLogger(false));
  console.log(`Validated ${proofFeed ? 'private proof' : 'production'} configuration: ${JSON.stringify(effective.publish)}`);
  process.exit(0);
}
for (const args of [
  [join(desktop, 'node_modules/typescript/bin/tsc'), '-p', desktop, '--outDir', compiled],
  [join(desktop, 'scripts/pack_web.mjs'), join(compiled, 'packed')],
]) {
  const result = spawnSync(process.execPath, args, { stdio: 'inherit' });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
await build({
  projectDir: desktop,
  targets: Platform.MAC.createTarget(['dmg', 'zip'], Arch.arm64),
  publish: 'never',
  config: configurationPath,
});

// Carry the native bridge capability beside the artifact checksum. The updater uses
// this fact before it offers a required-shell restart for a server's new screens.
const { DESKTOP_BRIDGE_VERSION } = createRequire(import.meta.url)(join(compiled, 'bridge_version.js'));
if (!Number.isInteger(DESKTOP_BRIDGE_VERSION) || DESKTOP_BRIDGE_VERSION < 1)
  throw new Error('The compiled shell must export a positive DESKTOP_BRIDGE_VERSION.');
appendFileSync(join(output, 'dist/latest-mac.yml'), `\npanelsBridgeVersion: ${DESKTOP_BRIDGE_VERSION}\n`);
