/** The SDK owns child metadata, not changes to the Panels server environment. */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';

type ClaudeSdk = typeof import('@anthropic-ai/claude-agent-sdk');
const package_file = join(
  dirname(createRequire(import.meta.url).resolve('@anthropic-ai/claude-agent-sdk')),
  'package.json',
);
export const CLAUDE_AGENT_SDK_VERSION = (
  JSON.parse(readFileSync(package_file, 'utf8')) as { version: string }
).version;
let imported: Promise<ClaudeSdk> | null = null;

export function load_claude_sdk(): Promise<ClaudeSdk> {
  if (imported === null) {
    const inherited = process.env.NoDefaultCurrentDirectoryInExePath;
    imported = import('@anthropic-ai/claude-agent-sdk')
      .finally(() => {
        if (inherited === undefined) delete process.env.NoDefaultCurrentDirectoryInExePath;
        else process.env.NoDefaultCurrentDirectoryInExePath = inherited;
      })
      .then((sdk) => {
        const query: ClaudeSdk['query'] = (parameters) => {
          const inherited_version = process.env.CLAUDE_AGENT_SDK_VERSION;
          try {
            return sdk.query(parameters);
          } finally {
            if (inherited_version === undefined) delete process.env.CLAUDE_AGENT_SDK_VERSION;
            else process.env.CLAUDE_AGENT_SDK_VERSION = inherited_version;
          }
        };
        return { ...sdk, query };
      });
  }
  return imported;
}
