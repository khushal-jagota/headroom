/** Scripted hosts inspect their real child through the same native evidence as production. */
import { ChildProcesses, OneShotChildren } from '../../lifecycle/owned_processes.ts';
import { create_worker_supervision_runtime } from '../../worker_supervision/composition.ts';
import { LocalChildCustody } from '../child_custody.ts';

export class ScriptedCustody {
  readonly processes = new ChildProcesses();
  readonly one_shot_children = new OneShotChildren(this.processes);
  readonly runtime = create_worker_supervision_runtime({
    one_shot_children: this.one_shot_children,
  });
  readonly inspection = new LocalChildCustody({
    runtime: this.runtime,
    child_processes: this.processes,
    one_shot_children: this.one_shot_children,
  });
  async close(): Promise<void> {
    await this.runtime.close();
    this.one_shot_children.seal();
    this.processes.seal();
    await this.one_shot_children.settled();
    await this.processes.settled();
  }
}
