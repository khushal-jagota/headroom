import type { ReadSql } from '../core/database/contracts.ts';
import type { WorkerTypeDefinition } from './contracts.ts';
import { WorkerTypeRegistry, KNOWN_TOOLSET_PROFILES } from './registry.ts';
import { read_definitions, worker_types_table_exists } from './store.ts';
export interface ConfiguredWorkerRuntimeDefinitions { readonly worker_type_registry: WorkerTypeRegistry }
export function build_worker_runtime_definitions(worker_type_definitions:readonly WorkerTypeDefinition[],known_skills:ReadonlySet<string>,known_toolset_profiles=KNOWN_TOOLSET_PROFILES):ConfiguredWorkerRuntimeDefinitions {
  return Object.freeze({worker_type_registry:new WorkerTypeRegistry(worker_type_definitions,known_skills,known_toolset_profiles)});
}
let configured=build_worker_runtime_definitions([],new Set());
export function load_worker_runtime_definitions(sql:ReadSql):ConfiguredWorkerRuntimeDefinitions {
  if (!worker_types_table_exists(sql)) return configured;
  configured=build_worker_runtime_definitions(read_definitions(sql),new Set(sql.all<{skill_name:string}>('SELECT skill_name FROM managed_skills ORDER BY skill_name').map(r=>r.skill_name)));
  return configured;
}
export function configured_worker_runtime_definitions():ConfiguredWorkerRuntimeDefinitions{return configured;}
export function configured_worker_type_registry():WorkerTypeRegistry{return configured.worker_type_registry;}
