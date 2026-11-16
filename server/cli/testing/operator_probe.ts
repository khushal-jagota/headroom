/** Fixed preload for the existing owned-process fixture. No service starts. */
import { operator_commands } from '../../environments/commands.ts';
import { OperatorInterrupted } from '../../environments/command_contracts.ts';
for (const [name, signal] of [
  ['restart', 'SIGINT'],
  ['serve', 'SIGTERM'],
] as const)
  Reflect.set(operator_commands[name], 'handler', async () => {
    const prefix = signal === 'SIGTERM' ? 'T'.repeat(2 * 1024 * 1024) : 'retained prefix\n';
    throw new OperatorInterrupted(signal, prefix, new Error('Recorded cleanup'));
  });
