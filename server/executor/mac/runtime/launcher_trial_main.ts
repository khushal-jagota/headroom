import { supervise_launcher } from './launcher_trial.ts';
if (!process.argv[2] || process.platform !== 'darwin')
  throw new Error('Expected a Mac launcher trial.');
await supervise_launcher(process.argv[2]);
