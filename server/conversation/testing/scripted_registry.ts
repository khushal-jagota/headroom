/** One shared scripted process per child, including its actual retired wire observations. */
import type { BackendChildFactory } from '../backends/contracts.ts';
import type { ConversationCoreOptions } from '../runtime/contracts.ts';
import type { ScriptedBehavior, ScriptedSnapshot } from './scripted_protocol.ts';
import { ScriptedChild } from './scripted_child.ts';

export class ScriptedBackendRegistry {
  readonly factories: ConversationCoreOptions['backend_child_factories'];
  private readonly current = new Map<string, ScriptedChild>();
  private readonly children = new Map<ScriptedChild, string>();
  private readonly retired = new Set<ScriptedChild>();
  private readonly ready = new Map<ScriptedChild, Promise<void>>();
  private readonly pending = new Map<string, { command: string; behavior: ScriptedBehavior }[]>();

  constructor() {
    const factory: BackendChildFactory = ({ resolved_start, event_sink }) => {
      const id = resolved_start.conversation_id;
      const child = new ScriptedChild(event_sink);
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
    this.factories = { codex: factory, claude: factory, hermes: factory };
  }

  child(conversation_id: string): ScriptedChild {
    const child = this.current.get(conversation_id);
    if (!child) throw new Error(`No scripted child exists for ${conversation_id}`);
    return child;
  }

  async configure(id: string, command: string, behavior: ScriptedBehavior): Promise<void> {
    const child = this.current.get(id);
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
    this.current.clear();
    this.children.clear();
    this.ready.clear();
    this.retired.clear();
    this.pending.clear();
  }
}
