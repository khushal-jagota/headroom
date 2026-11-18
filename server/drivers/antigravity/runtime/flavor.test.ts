import { RequestError } from '@agentclientprotocol/sdk/unstable';
import { expect, test } from 'vitest';
import { ConversationAccess, ResolvedConversationStart } from '../../../conversation/contracts.ts';
import { parse_backend_instance_id } from '../../core/identity.ts';
import { acp_child_environment } from '../../acp/environment.ts';
import { ANTIGRAVITY_SIGN_IN_REQUIRED, create_antigravity_flavor } from './flavor.ts';

const flavor = create_antigravity_flavor({
  executable: '/usr/local/bin/agy',
  home: '/home/owner/.gemini',
  worker_process_root: '/srv/panels/workers',
  panels_server_url: 'http://127.0.0.1:9999',
});
const start = new ResolvedConversationStart({
  conversation_id: 'conversation-1',
  backend_key: parse_backend_instance_id('antigravity'),
  model: 'gemini-4-argon',
  reasoning_effort: 'medium',
  role_materials: null,
  workspace_folder: '/srv/work',
  access: ConversationAccess.full,
});

// Risk: a full-access conversation stalls on permission asks, agy writes its token to a keyring
// or another state directory, or a Google credential from the server's environment signs agy
// in as someone else.
test('a full-access launch skips permissions, keeps its own state, and inherits no credential', () => {
  const launch = flavor.launch(start);
  expect(launch.argv).toEqual([
    '/usr/local/bin/agy',
    'acp',
    '--gemini-dir',
    '/home/owner/.gemini',
    '--app-data-dir',
    'antigravity-acp',
    '--disable-telemetry',
    '--dangerously-skip-permissions',
  ]);
  const environment = acp_child_environment(launch.environment_overrides, start, {
    HOME: '/home/owner',
    PATH: '/usr/bin',
    GEMINI_API_KEY: 'gemini-key',
    GOOGLE_API_KEY: 'google-key',
    GOOGLE_APPLICATION_CREDENTIALS: '/home/owner/adc.json',
    GOOGLE_CLOUD_PROJECT: 'someone-else',
  });
  expect(environment).toMatchObject({
    GEMINI_HOME: '/home/owner/.gemini',
    AGY_ACP_FORCE_FILE_STORAGE: '1',
    BROWSER: 'false',
  });
  expect(Object.keys(environment).filter((name) => /GEMINI_API|GOOGLE/.test(name))).toEqual([]);
});

// Risk: a signed-out agy reads as a broken session instead of asking for a sign-in.
test('only ACP auth_required means agy needs a sign-in', () => {
  expect(flavor.requires_sign_in!(new RequestError(-32000, 'Authentication required'))).toBe(
    ANTIGRAVITY_SIGN_IN_REQUIRED,
  );
  expect(flavor.requires_sign_in!(new RequestError(-32603, 'Internal error'))).toBeNull();
  expect(flavor.requires_sign_in!(new Error('Authentication required'))).toBeNull();
});
