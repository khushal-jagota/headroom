/** Settings on installed iOS builds 9 and 10 still reads this key, directly or by revision. */
import type { ResourceProvider } from '../../resource_versions/contracts.ts';
import type { FileUpdateAttemptStore } from './attempts.ts';
import { add_seconds, instant } from './logic/experience.ts';

interface DeploymentStatus {
  state: 'idle' | 'preparing' | 'restarting' | 'problem' | 'back_up';
  deployed_sha: string | null;
  target_sha: string | null;
  outcome: 'failed' | 'succeeded' | null;
  detail: string | null;
  valid_until: string | null;
  success_display_until: string | null;
}

export function legacy_deployment(attempts: FileUpdateAttemptStore, app_sha: string | null) {
  function status(): DeploymentStatus {
    const attempt = attempts.read();
    const idle: DeploymentStatus = {
      state: 'idle',
      deployed_sha: app_sha,
      target_sha: null,
      outcome: null,
      detail: null,
      valid_until: null,
      success_display_until: null,
    };
    if (!attempt || attempt.state === 'waiting') return idle;
    idle.target_sha = attempt.to_sha;
    if (attempt.state === 'preparing') return { ...idle, state: 'preparing' };
    if (attempt.state === 'installing')
      return { ...idle, state: 'restarting', valid_until: attempt.deadline_at };
    if (attempt.state === 'done' && attempt.to_sha !== app_sha) return idle;
    const until = add_seconds(instant(attempt.updated_at), 300);
    const fresh = BigInt(Date.now()) * 1000n < until.epochMicroseconds;
    if (attempt.state === 'failed')
      return fresh
        ? { ...idle, state: 'problem', outcome: 'failed', detail: attempt.detail }
        : idle;
    return fresh
      ? {
          ...idle,
          state: 'back_up',
          outcome: 'succeeded',
          valid_until: until.iso,
          success_display_until: until.iso,
        }
      : { ...idle, outcome: 'succeeded' };
  }
  const resource: ResourceProvider = {
    capture(_sql, _request, key) {
      if (key !== '/api/deployment-status') return null;
      const body = status();
      return { revision: JSON.stringify(body), read: () => body };
    },
  };
  return { status, resource };
}
