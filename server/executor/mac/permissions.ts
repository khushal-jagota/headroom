/** Consent preview is lexical. Only the explicit check touches the selected paths. */
import { randomUUID } from 'node:crypto';
import { opendir, open, realpath, stat, unlink } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import type { CommandResult, Invocation } from '../../cli/contracts.ts';
import { human_error } from '../../cli/errors.ts';
import { path_is_within } from '../../core/path_containment.ts';
import { expand_executor_path } from './platform.ts';

function description(path: string, home: string): string {
  for (const directory of ['Desktop', 'Documents', 'Downloads'])
    if (path_is_within(join(home, directory), path))
      return `${directory}: macOS can request Files and Folders consent.`;
  return 'Ordinary account access; a redirected or protected path can require separate consent.';
}
async function check_access(workspace: string, output: string): Promise<[string, string]> {
  workspace = await realpath(workspace);
  output = await realpath(output);
  if (!(await stat(workspace)).isDirectory() || !(await stat(output)).isDirectory())
    throw new Error('workspace and output must be existing directories');
  const entries = await opendir(workspace);
  try {
    await entries.read();
  } finally {
    await entries.close();
  }
  const temporary = join(output, 'panels-access-' + randomUUID());
  const file = await open(temporary, 'wx+', 0o600);
  try {
    const content = Buffer.from('Panels access check\n');
    await file.writeFile(content);
    const result = Buffer.alloc(content.length);
    const { bytesRead } = await file.read(result, 0, result.length, 0);
    if (bytesRead !== content.length || !result.equals(content))
      throw new Error('output read did not match the temporary write');
  } finally {
    try {
      await file.close();
    } finally {
      await unlink(temporary);
    }
  }
  return [workspace, output];
}
export async function permissions(invocation: Invocation): Promise<CommandResult> {
  const home = expand_executor_path('~', invocation.environment);
  const workspace = resolve(
    expand_executor_path(invocation.values.workspace as string, invocation.environment),
  );
  const output = resolve(
    expand_executor_path(invocation.values.output as string, invocation.environment),
  );
  const lines = [
    `Workspace directory read: ${workspace}`,
    description(workspace, home),
    `Output temporary write/read/remove: ${output}`,
    path_is_within(workspace, output)
      ? 'Output reuses the workspace scope, subject to macOS protections.'
      : description(output, home),
    'No Full Disk Access or unrelated permission is requested. Python consent does not transfer automatically.',
    'If macOS requests consent, you decide. Denial remains effective. Panels does not grant or reset access.',
  ];
  if (!invocation.values.check) {
    lines.push(
      'Preview only. Symlinks remain unresolved. Use --check only after you approve these actions.',
    );
  } else {
    lines.push(
      'Explicit check: path resolution can itself require consent, including for redirected paths.',
    );
    try {
      const [checked_workspace, checked_output] = await check_access(workspace, output);
      lines.push(
        `Checked workspace: ${checked_workspace}`,
        `Checked output: ${checked_output}`,
        'Directory and temporary file access passed. This does not prove provider attribution or future consent persistence.',
      );
    } catch (error) {
      if (!(error instanceof Error)) throw error;
      return {
        ...human_error(
          invocation.command,
          `Access check failed: ${error.message}. Review the selected paths and account access. ` +
            'For a denied macOS grant, use System Settings > Privacy & Security > Files and Folders. No retry or permission reset occurred.',
        ),
        stdout: lines.join('\n') + '\n',
      };
    }
  }
  return { exit_code: 0, stdout: lines.join('\n') + '\n', stderr: '' };
}
