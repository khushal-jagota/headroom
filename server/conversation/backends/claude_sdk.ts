/** The pinned SDK changes one process variable at import; Panels preserves inheritance. */
type ClaudeSdk = typeof import('@anthropic-ai/claude-agent-sdk');
let imported: Promise<ClaudeSdk> | null = null;

export function load_claude_sdk(): Promise<ClaudeSdk> {
  if (imported === null) {
    const inherited = process.env.NoDefaultCurrentDirectoryInExePath;
    imported = import('@anthropic-ai/claude-agent-sdk').finally(() => {
      if (inherited === undefined) delete process.env.NoDefaultCurrentDirectoryInExePath;
      else process.env.NoDefaultCurrentDirectoryInExePath = inherited;
    });
  }
  return imported;
}
