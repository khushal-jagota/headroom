import { createHash } from 'node:crypto';
import type { FastifyRequest } from 'fastify';
import type { ReadSql } from '../core/database/contracts.ts';
import type { ResourceProvider, ResourceSnapshot } from '../resource_versions/contracts.ts';
import { configured_worker_type_registry } from './configuration.ts';
import type { WorkerTypeRegistry } from './registry.ts';
import { python_json_dumps } from './store.ts';
export class WorkerTypeResources implements ResourceProvider {
  private cachedRegistry:WorkerTypeRegistry|null=null;
  private readonly cached=new Map<string,ResourceSnapshot>();
  capture(_sql:ReadSql,_request:FastifyRequest,key:string):ResourceSnapshot|null {
    const registry=configured_worker_type_registry();
    if (registry!==this.cachedRegistry) {this.cached.clear();this.cachedRegistry=registry;}
    let snapshot=this.cached.get(key);
    if (!snapshot) {
      let subject:string[]|null=null;
      if (key==='/api/worker-types') subject=registry.registered_worker_types();
      else if (key==='/api/worker-types?supported=true') subject=registry.supported_worker_types();
      else if (key.startsWith('/api/worker-types/') && key.endsWith('/manifest')) {
        const worker_type=key.slice('/api/worker-types/'.length,-'/manifest'.length);
        if (registry.registered_worker_types().includes(worker_type)) subject=[worker_type];
      }
      if (subject===null) return null;
      const body={worker_types:subject.map(name=>registry.manifest(name))};
      snapshot={revision:createHash('sha256').update(python_json_dumps(body,true,true)).digest('hex'),read:()=>body};
      this.cached.set(key,snapshot);
    }
    return snapshot;
  }
}
