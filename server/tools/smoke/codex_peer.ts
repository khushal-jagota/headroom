/** The scripted Codex app server that answers the smoke's conversation on the default backend. */
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';

/** Writes the script and the `codex` executable under root/bin. Returns the peer's environment. */
export async function write_default_backend_peer(
  root: string,
  source: string,
): Promise<Record<string, string>> {
  const script = join(root, 'codex-script.json');
  const producer = JSON.stringify(
    join(source, 'tests/support/conversation_codex_scripted_app_server.py'),
  );
  const actions = [
    { do: 'agent_delta', item_id: 'smoke-answer', text: 'Production smoke reply.' },
    {
      do: 'item_completed',
      item: { type: 'agentMessage', id: 'smoke-answer', text: 'Production smoke reply.' },
    },
    { do: 'complete', status: 'completed' },
  ];
  await writeFile(
    script,
    JSON.stringify({
      transcript_path: join(root, 'codex-transcript.jsonl'),
      thread_id: 'production-smoke-thread',
      turns: [{ actions }],
    }) + '\n',
    { flag: 'wx', mode: 0o600 },
  );
  await writeFile(
    join(root, 'bin/codex'),
    `#!${join(source, '.venv/bin/python')}\n` +
      'import runpy, sys\n' +
      "if sys.argv[1:] == ['--version']:\n" +
      "    print('codex-cli 0.148.0')\n" +
      'else:\n' +
      `    runpy.run_path(${producer}, run_name='__main__')\n`,
    { flag: 'wx', mode: 0o700 },
  );
  return { PANELS_CODEX_SCRIPTED_APP_SERVER_SCRIPT: script };
}
