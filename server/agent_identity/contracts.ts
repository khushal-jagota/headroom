import type { Principal } from '../core/contracts.ts';

export const LAUNCH_TOKEN_ENVIRONMENT_NAME = 'PLAN_LAUNCH_TOKEN';
export const UNIDENTIFIED_LAUNCH_TOKEN = 'unidentified';
export interface AgentIdentity {
  principal: Principal;
  conversation_id: string;
}
