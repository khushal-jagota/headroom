import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { readFileSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..');
const repositoryRoot = join(webRoot, '..');
const contractPath = join(webRoot, 'src', 'lib', 'acp', 'contracts.ts');
const conversationStatePath = join(webRoot, 'src', 'lib', 'acp', 'conversationState.ts');
const conversationControllerPath = join(webRoot, 'src', 'lib', 'acp', 'conversationController.ts');
const transportPath = join(webRoot, 'src', 'lib', 'acp', 'panelsTransport.ts');
const donorStorePath = join(webRoot, 'src', 'vendor', 'acp-components-core', 'src', 'store', 'sessionStore.ts');
const upstreamPath = join(webRoot, 'src', 'vendor', 'acp-components-core', 'UPSTREAM.md');
const serverFixturePath = join(repositoryRoot, 'tests', 'fixtures', 'acp', 'server-envelopes-v1.json');
const browserFixturePath = join(repositoryRoot, 'tests', 'fixtures', 'acp', 'browser-actions-v1.json');

const packageJson = JSON.parse(readFileSync(join(webRoot, 'package.json'), 'utf8'));
const packageLock = JSON.parse(readFileSync(join(webRoot, 'package-lock.json'), 'utf8'));
assert.equal(packageJson.dependencies['@agentclientprotocol/sdk'], '1.2.1');
assert.equal(packageJson.dependencies.zustand, '5.0.13');
assert.equal(packageJson.dependencies.zod, '4.4.3');
for (const forbidden of ['@acp-components/core', 'react', 'react-dom', 'acp-ui']) {
  assert.equal(packageJson.dependencies[forbidden], undefined, `${forbidden} must not be a dependency`);
  assert.equal(packageJson.devDependencies[forbidden], undefined, `${forbidden} must not be a dev dependency`);
  assert.equal(packageLock.packages[`node_modules/${forbidden}`], undefined, `${forbidden} must not be installed`);
}
assert.deepEqual(
  {
    sdk: packageLock.packages['node_modules/@agentclientprotocol/sdk'].version,
    zustand: packageLock.packages['node_modules/zustand'].version,
    zod: packageLock.packages['node_modules/zod'].version,
  },
  { sdk: '1.2.1', zustand: '5.0.13', zod: '4.4.3' },
);
assert.equal(
  packageLock.packages['node_modules/@agentclientprotocol/sdk'].integrity,
  'sha512-jwYUdOQR7tc+Zfch53VL4JJyUNK/46q03uUTYb+PjECsmnNl94XFXOfYLJ8RBpMNidXd1rpOAVgb0vqD98xImA==',
);

const contractSource = readFileSync(contractPath, 'utf8');
const conversationStateSource = readFileSync(conversationStatePath, 'utf8');
const conversationControllerSource = readFileSync(conversationControllerPath, 'utf8');
const transportSource = readFileSync(transportPath, 'utf8');
const donorStoreSource = readFileSync(donorStorePath, 'utf8');
const upstreamSource = readFileSync(upstreamPath, 'utf8');
assert.match(contractSource, /from '@agentclientprotocol\/sdk'/);
assert.match(contractSource, /from '\.\.\/\.\.\/vendor\/acp-components-core\/src\/types\/index'/);
assert.match(contractSource, /import type \{ Message, Session \} from/);
for (const importedName of [
  'PromptRequest',
  'RequestPermissionRequest',
  'RequestPermissionResponse',
  'SessionNotification',
  'TerminalOutputResponse',
]) {
  assert.match(contractSource, new RegExp(`\\b${importedName}\\b`));
}
const sdkOwnedNames = [
  'SessionNotification',
  'SessionUpdate',
  'PromptRequest',
  'RequestPermissionRequest',
  'RequestPermissionResponse',
  'PermissionOption',
  'ContentBlock',
  'ToolCall',
  'ToolCallUpdate',
  'PlanEntry',
  'AvailableCommand',
  'UsageUpdate',
];
for (const name of sdkOwnedNames) {
  assert.doesNotMatch(
    contractSource,
    new RegExp(`(?:export\\s+)?(?:type|interface)\\s+${name}\\b`),
    `${name} must remain SDK-owned`,
  );
}

assert.equal(
  conversationStateSource.match(/switch \(update\.sessionUpdate\)/g)?.length,
  1,
  'one production SessionUpdate dispatcher owns the ACP fold',
);
assert.match(conversationControllerSource, /reduceConversationState/);
assert.doesNotMatch(contractSource, /\bsummary\??:\s*string/);
assert.doesNotMatch(conversationStateSource, /compaction_expanded|compaction.*expanded|expanded.*compaction/i);
assert.doesNotMatch(conversationControllerSource, /setCompactionExpanded/);
assert.doesNotMatch(
  [...Object.values({ conversationStateSource, conversationControllerSource, transportSource }),
    ...[]].join('\n'),
  /\/api\/chat\/commands|_acp\/skills\/list|resourceCatalogue|neutralPane|JSON-RPC|acp-ui|\bReact\b/,
);
assert.match(conversationStateSource, /from '\.\.\/\.\.\/vendor\/acp-components-core\/src\/types\/index'/);
assert.doesNotMatch(conversationStateSource, /(?:type|interface)\s+(?:Message|ToolCallState|PlanEntry)\b/);
assert.match(donorStoreSource, /export function appendSessionThought/);
assert.match(donorStoreSource, /expanded: false/);
const donorStoreHash = createHash('sha256').update(donorStoreSource).digest('hex');
assert.equal(donorStoreHash, 'bc6f745bdab898243aa5566e3333a968ba1c52b95156ff4b57a0a6e912203aaa');
assert.match(upstreamSource, new RegExp(donorStoreHash));
for (const correction of [
  'exported pure helpers',
  'default closed',
  'stable plan part',
  'reconcile by tool-call ID',
  'preserve UI-only expansion',
]) {
  assert.match(upstreamSource, new RegExp(correction));
}

const serverFixtureText = readFileSync(serverFixturePath, 'utf8');
const browserFixtureText = readFileSync(browserFixturePath, 'utf8');
const serverEnvelopes = JSON.parse(serverFixtureText);
const browserActions = JSON.parse(browserFixtureText);
assert.deepEqual(JSON.parse(JSON.stringify(serverEnvelopes)), serverEnvelopes);
assert.deepEqual(JSON.parse(JSON.stringify(browserActions)), browserActions);

assert.deepEqual(
  [...new Set(serverEnvelopes.map((envelope) => envelope.type))],
  [
    'acp_session_update',
    'activity',
    'delivery_receipt',
    'queue_snapshot',
    'context_compaction',
    'permission_request',
    'permission_outcome',
    'connection',
    'protocol_update_rejected',
    'human_echo',
    'terminal_state',
  ],
);
assert.deepEqual(
  browserActions.map((action) => action.type),
  ['attach', 'prompt', 'cancel', 'new_conversation', 'permission_response'],
);

const terminalState = serverEnvelopes.find((envelope) => envelope.type === 'terminal_state');
assert.deepEqual(terminalState.payload, {
  lifecycle: 'active',
  terminalId: 'terminal-1',
  terminalOutput: {
    exitStatus: { exitCode: 0 },
    output: 'line one\nline two\n',
    truncated: true,
  },
});

const connectionStates = serverEnvelopes
  .filter((envelope) => envelope.type === 'connection')
  .map((envelope) => [envelope.payload.state, envelope.payload.supportsSteer]);
assert.deepEqual(connectionStates, [
  ['reset', true],
  ['ready', true],
  ['closed', false],
  ['error', false],
]);

const updates = serverEnvelopes
  .filter((envelope) => envelope.type === 'acp_session_update')
  .map((envelope) => envelope.payload.update);
assert.deepEqual(
  updates.map((update) => update.sessionUpdate),
  [
    'user_message_chunk',
    'user_message_chunk',
    'agent_thought_chunk',
    'agent_message_chunk',
    'tool_call',
    'plan',
    'available_commands_update',
    'usage_update',
  ],
);
const thoughtUpdates = updates.filter((update) => update.sessionUpdate === 'agent_thought_chunk');
assert.equal(thoughtUpdates.length, 1);
assert.equal(
  updates.some(
    (update) =>
      update.sessionUpdate === 'agent_message_chunk' &&
      JSON.stringify(update.content) === JSON.stringify(thoughtUpdates[0].content),
  ),
  false,
  'thought content must not be duplicated as assistant text',
);

const generatedTypecheckPath = join(webRoot, 'tests', `.acp-contract-fixtures-${process.pid}.ts`);
const generatedSource = `
import type { BrowserAction, ConnectionPayload, ContextCompaction, ServerEnvelope } from '../src/lib/acp/contracts';
import type { ConversationSnapshot } from '../src/lib/acp/conversationState';

const serverEnvelopes = ${serverFixtureText.trim()} satisfies ServerEnvelope[];
const browserActions = ${browserFixtureText.trim()} satisfies BrowserAction[];
const serverDiscriminators = ${JSON.stringify(serverEnvelopes.map((value) => value.type))} as const satisfies readonly ServerEnvelope['type'][];
const browserDiscriminators = ${JSON.stringify(browserActions.map((value) => value.type))} as const satisfies readonly BrowserAction['type'][];
void serverDiscriminators;
void browserDiscriminators;

// @ts-expect-error supportsSteer is required.
const missingSteerCapability: ConnectionPayload = { state: 'ready', detail: 'Ready' };
const nullSteerCapability: ConnectionPayload = {
  state: 'ready',
  detail: 'Ready',
  // @ts-expect-error supportsSteer accepts a real boolean only.
  supportsSteer: null,
};
const stringSteerCapability: ConnectionPayload = {
  state: 'ready',
  detail: 'Ready',
  // @ts-expect-error supportsSteer accepts a real boolean only.
  supportsSteer: 'true',
};
const integerSteerCapability: ConnectionPayload = {
  state: 'ready',
  detail: 'Ready',
  // @ts-expect-error supportsSteer accepts a real boolean only.
  supportsSteer: 1,
};
const extraSteerCapability: ConnectionPayload = {
  state: 'ready',
  detail: 'Ready',
  supportsSteer: true,
  // @ts-expect-error capability dictionaries and extra capability fields are forbidden.
  supportsQueue: true,
};
void missingSteerCapability;
void nullSteerCapability;
void stringSteerCapability;
void integerSteerCapability;
void extraSteerCapability;

const completedCompaction: ContextCompaction = {
  boundaryId: 'boundary-1',
  state: 'compacted',
  trigger: 'explicit',
};
const compactionWithPrivateContext: ContextCompaction = {
  boundaryId: 'boundary-2',
  state: 'compacted',
  trigger: 'automatic',
  // @ts-expect-error compaction context is backend-private and absent from the public contract.
  summary: 'private context',
};
void completedCompaction;
void compactionWithPrivateContext;

declare const readonlySnapshot: ConversationSnapshot;
// @ts-expect-error public transcript arrays are readonly.
readonlySnapshot.session.messages.push({});
// @ts-expect-error public tool records are readonly.
readonlySnapshot.session.pendingToolCalls['new-tool'] = readonlySnapshot.session.pendingToolCalls['existing'];
// @ts-expect-error nested public tool state is readonly.
readonlySnapshot.session.pendingToolCalls['existing'].status = 'failed';
`;
try {
  writeFileSync(generatedTypecheckPath, generatedSource);
  execFileSync(
    join(webRoot, 'node_modules', '.bin', 'tsc'),
    [
      '--noEmit',
      '--strict',
      '--skipLibCheck',
      '--module',
      'ESNext',
      '--moduleResolution',
      'Bundler',
      '--target',
      'ES2022',
      generatedTypecheckPath,
    ],
    { cwd: webRoot, stdio: 'inherit' },
  );
} finally {
  rmSync(generatedTypecheckPath, { force: true });
}

console.log('acp-contracts.test.mjs: all assertions passed');
