/** One shared scripted process per child, including its actual retired wire observations. */
import { registry_of_factories } from '../../drivers/testing/registry.ts';
import type { BackendChildFactory } from '../backends/contracts.ts';
import type { DriverRegistry } from '../../drivers/core/registry.ts';
import type { ScriptedBehavior, ScriptedSnapshot } from './scripted_protocol.ts';
import { ScriptedChild } from './scripted_child.ts';
import { ScriptedCustody } from './scripted_custody.ts';
import type { WorkerSupervisionRuntime } from '../../worker_supervision/runtime.ts';

export class ScriptedBackendRegistry {
  private owned_custody: ScriptedCustody | null = null;
  private runtime: WorkerSupervisionRuntime | null = null;
  get custody(): ScriptedCustody {
    return (this.owned_custody ??= new ScriptedCustody());
  }
  bind_runtime(runtime: WorkerSupervisionRuntime): void {
    if (this.children.size !== 0 || this.owned_custody !== null)
      throw new Error('Scripted custody must bind before its first child');
    this.runtime = runtime;
  }
  readonly registry: DriverRegistry;
  private readonly current = new Map<string, ScriptedChild>();
  private readonly children = new Map<ScriptedChild, string>();
  private readonly retired = new Set<ScriptedChild>();
  private readonly ready = new Map<ScriptedChild, Promise<void>>();
  private readonly pending = new Map<string, { command: string; behavior: ScriptedBehavior }[]>();

  constructor() {
    const factory: BackendChildFactory = ({ resolved_start, event_sink }) => {
      const id = resolved_start.conversation_id;
      const child = new ScriptedChild(event_sink, this.runtime ?? this.custody.runtime);
      this.current.set(id, child);
      this.children.set(child, id);
      const queued = this.pending.get(id) ?? [];
      this.pending.delete(id);
      const prepare = async () => {
        for (const item of queued) await child.configure(item.command, item.behavior);
      };
      const ready = prepare();
      this.ready.set(child, ready);
      const start = child.start.bind(child);
      child.start = async (...args) => {
        await ready;
        await start(...args);
      };
      for (const name of ['stop', 'detach'] as const) {
        const original = child[name].bind(child);
        child[name] = async () => {
          await ready;
          await original();
          this.retired.add(child);
          if (this.current.get(id) === child) this.current.delete(id);
        };
      }
      return child;
    };
    this.registry = registry_of_factories({ codex: factory, claude: factory, hermes: factory });
  }

  child(conversation_id: string): ScriptedChild {
    const child = this.current.get(conversation_id);
    if (!child) throw new Error(`No scripted child exists for ${conversation_id}`);
    return child;
  }

  async configure(
    id: string,
    command: string,
    behavior: ScriptedBehavior,
    target: 'current' | 'next' = 'current',
  ): Promise<void> {
    const child = target === 'next' ? undefined : this.current.get(id);
    if (child) {
      await this.ready.get(child);
      await child.configure(command, behavior);
    } else {
      const queued = this.pending.get(id) ?? [];
      queued.push({ command, behavior });
      this.pending.set(id, queued);
    }
  }

  async inspect(id: string): Promise<readonly ScriptedSnapshot[]> {
    const snapshots: ScriptedSnapshot[] = [];
    for (const [child, conversation_id] of this.children) {
      if (conversation_id !== id) continue;
      await this.ready.get(child);
      const snapshot = this.retired.has(child) ? child.last_observation : await child.inspect();
      if (snapshot === null) throw new Error('A retired child has no process observation');
      snapshots.push(snapshot);
    }
    return snapshots;
  }

  async close(): Promise<void> {
    await Promise.allSettled(this.ready.values());
    await Promise.all([...this.children.keys()].map((child) => child.terminate()));
    await this.owned_custody?.close();
    this.current.clear();
    this.children.clear();
    this.ready.clear();
    this.retired.clear();
    this.pending.clear();
  }
}
