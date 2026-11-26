/** Claude's plan limits, read through the selected CLI so it uses its own login store. */
import type { OwnedOneShotChildren } from '../../../lifecycle/contracts.ts';
import { run_claude_probe_session } from './probe_session.ts';
import type { ClaudeProbeSessionOptions } from './probe_session.ts';

export const CLAUDE_USAGE_TIMEOUT_SECONDS = 30;
class ClaudeUsageUnavailable extends Error {}

/** The CLI's `get_usage` answer, unparsed. The transcript scan behind `behaviors` is skipped. */
export function read_claude_usage(
  claude_executable: string,
  children: OwnedOneShotChildren,
  options: ClaudeProbeSessionOptions = {},
): Promise<unknown> {
  return run_claude_probe_session(claude_executable, children, options, {
    timeout_seconds: CLAUDE_USAGE_TIMEOUT_SECONDS,
    timed_out_message: 'claude did not answer its usage request in time',
    unavailable: ClaudeUsageUnavailable,
    read: async (cli): Promise<unknown> => {
      await cli.initializationResult();
      return cli.usage_EXPERIMENTAL_MAY_CHANGE_DO_NOT_RELY_ON_THIS_API_YET({ skipBehaviors: true });
    },
  });
}
