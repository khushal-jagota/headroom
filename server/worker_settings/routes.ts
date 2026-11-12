import type { FastifyPluginAsync } from 'fastify';
import { dirname, resolve } from 'node:path';
import type { AreaServices } from '../core/authority/register.ts';
import { requireOwnerOrChief } from '../core/authority/identity.ts';
import { PlannerError } from '../core/contracts.ts';
import { dictionaryBody, scalarQueries } from '../list_reads/http.ts';
import { configured_worker_type_registry } from '../worker_types/configuration.ts';
import { definition_from_json, definition_to_json, read_definition } from '../worker_types/store.ts';
import * as skills from '../skills/service.ts';
import { stale_references } from '../skills/staleness.ts';
import * as service from './service.ts';
import { add_chief_conversation_signals } from './signals.ts';
export const routes: FastifyPluginAsync<AreaServices> = async (app, services) => {
  const { database, clock } = services;
  const options = () => ({ now: clock.nowUnix(), database_parent: dirname(resolve(services.config.db_path)) });
  app.get('/workers', async () => {
    const roster = database.read(sql => ({ workers: service.read_worker_management_index(configured_worker_type_registry()), chief_of_staff: service.read_chief_settings(sql) }));
    return { ...roster, chief_of_staff: await add_chief_conversation_signals(database, services.conversationSystem, roster.chief_of_staff) };
  });
  app.get('/skills/stale', () => database.read(sql => {
    const references = stale_references(sql);
    return { stale_references: references, skill_names: [...new Set(references.map(reference => reference.skill_name))].sort() };
  }));
  app.get('/skills', () => database.read(skills.read_skills_home));
  app.post('/skills/apply', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return { skills: skills.apply_skills(database, request.body, options()) }; });
  app.get<{ Params: { skill_name: string } }>('/skills/:skill_name', request => database.read(sql => skills.read_skill(sql, request.params.skill_name)));
  app.patch<{ Params: { skill_name: string } }>('/skills/:skill_name', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return skills.save_skill(database, request.params.skill_name, request.body, options()); });
  app.delete<{ Params: { skill_name: string } }>('/skills/:skill_name', request => { skills.delete_skill(database, request.params.skill_name, options()); return { name: request.params.skill_name, deleted: true }; });
  app.put<{ Params: { skill_name: string } }>('/skills/:skill_name/files', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return { name: request.params.skill_name, path: skills.write_skill_file(database, request.params.skill_name, request.body, options()) }; });
  app.get('/workers/chief-of-staff/settings', () => database.read(service.read_chief_settings));
  app.put('/workers/chief-of-staff/recap', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; requireOwnerOrChief(services.identity(request)); return service.update_chief_recap(database, request.body); });
  app.put('/workers/chief-of-staff/launch-defaults', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return service.update_chief_launch_defaults(database, database.read(sql => service.resolve_launch_defaults(sql, request.body))); });
  for (const method of ['PUT', 'PATCH'] as const) {
    app.route({ method, url: '/workers/chief-of-staff/skill', handler: (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return service.save_chief_skill(database, request.body, options()); } });
    app.route<{ Params: { worker_type: string } }>({ method, url: '/workers/:worker_type/skill', handler: (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return service.save_specialist_skill(database, request.params.worker_type, request.body, options()); } });
  }
  app.put<{ Params: { worker_type: string } }>('/workers/:worker_type/launch-defaults', (request, reply) => { if (!dictionaryBody(request.body, reply)) return; return service.update_worker_launch_defaults(database, request.params.worker_type, database.read(sql => service.resolve_launch_defaults(sql, request.body)), clock.nowUnix()); });
  app.get<{ Params: { worker_type: string } }>('/workers/:worker_type', request => database.read(sql => service.read_worker_management_detail(sql, configured_worker_type_registry(), request.params.worker_type)));
  app.get<{ Params: { worker_type: string } }>('/worker-types/:worker_type', request => database.read(sql => JSON.parse(definition_to_json(read_definition(sql, request.params.worker_type)))));
  app.post('/worker-types', (request, reply) => {
    if (!dictionaryBody(request.body, reply)) return;
    const { skill = null, ...record } = request.body;
    const definition = definition_from_json(JSON.stringify(record));
    if (skill !== null && (typeof skill !== 'object' || Array.isArray(skill))) throw new PlannerError('validation', 'skill must be an object', {});
    service.save_worker_type_definition(database, definition, skill as Record<string, unknown> | null, options());
    return JSON.parse(definition_to_json(definition));
  });
  app.delete<{ Params: { worker_type: string } }>('/worker-types/:worker_type', request => { service.delete_worker_type_definition(database, request.params.worker_type); return { worker_type: request.params.worker_type, deleted: true }; });
  app.get<{ Querystring: { supported?: string } }>('/worker-types', (request, reply) => {
    const { supported } = scalarQueries(request.query);
    if (supported !== undefined && !['1', 'true', 'on', 'yes', '0', 'false', 'off', 'no'].includes(supported.toLowerCase())) return reply.code(422).send({ detail: [{ type: 'bool_parsing', loc: ['query', 'supported'], msg: 'Input should be a valid boolean, unable to interpret input', input: supported }] });
    const registry = configured_worker_type_registry();
    const names = supported && ['1', 'true', 'on', 'yes'].includes(supported.toLowerCase()) ? registry.supported_worker_types() : registry.registered_worker_types();
    return { worker_types: names.map(name => registry.manifest(name)) };
  });
  app.get<{ Params: { worker_type: string } }>('/worker-types/:worker_type/manifest', request => ({ worker_types: [configured_worker_type_registry().manifest(request.params.worker_type)] }));
};
