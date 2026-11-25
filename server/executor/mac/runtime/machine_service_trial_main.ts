import { supervise_machine_service } from './machine_service_trial.ts';

const [updates, request_id] = process.argv.slice(2);
if (!updates || !request_id) throw new Error('Expected the machine update directory.');
await supervise_machine_service(updates, request_id);
