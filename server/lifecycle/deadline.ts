import { ShutdownDeadlineExceeded, type StopOptions } from './contracts.ts';
import { observe_shutdown as observe_conversation_shutdown } from '../conversation/shutdown.ts';

/** Only observes. The caller must retain the original actual settlement promise. */
export function observe_shutdown(
  actual: Promise<unknown>,
  options: StopOptions,
  name: string,
): Promise<void> {
  return observe_conversation_shutdown(
    actual,
    options,
    new ShutdownDeadlineExceeded([name]).message,
    [name],
  );
}
