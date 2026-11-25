import { supervise_launcher } from './launcher_trial.ts';
if (!process.argv[2] || process.platform !== 'darwin')
  throw new Error('Expected a Mac launcher trial.');
try {
  await supervise_launcher(process.argv[2]);
} catch (error) {
  // A handled failure has already attempted bootout. A zero exit also stops
  // launchd's KeepAlive retry if unloading the job itself failed.
  console.error(error);
}
