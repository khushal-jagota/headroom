/** Route-required Manager contracts. Manager CRUD remains with the Manager area. */
import type { ConversationBackendKey } from '../conversation/contracts.ts';
export interface ManagerLaunchConfiguration {
    employee_backend: ConversationBackendKey;
    employee_launch_model: string;
    employee_launch_reasoning_effort: string | null;
}
export interface Manager {
    id: string;
    title: string;
    goal: string;
    recap: string;
    project_id: string;
    project_name: string;
    manager_launch_configuration: ManagerLaunchConfiguration;
    settled: boolean;
    created_at: number;
    updated_at: number;
    launch_configuration_is_explicit: boolean;
    page: unknown;
    page_revision: number;
    manager_agent_key: string;
}
export interface ManagerRead {
    item: Manager;
}
