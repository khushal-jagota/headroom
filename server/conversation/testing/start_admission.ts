/** One fixed populated lifecycle, evaluated against fresh Python by direct comparison. */
import { createDatabase } from '../../core/database/index.ts';
import { HOME_MACHINE_ID, ORIGINAL_MAC_MACHINE_ID } from '../../execution_identity.ts';
import { AutomaticStartAdmission } from '../start_admission/index.ts';
import type { StartRequest } from '../start_admission/contracts.ts';

export function run(database_path: string): unknown {
  const db = createDatabase(database_path);
  let now = 0;
  const make = () => {
    const admission = new AutomaticStartAdmission({ clock: () => now });
    admission.configure_destinations(() => true);
    return admission;
  };
  const request = (
    identity: string,
    destination = HOME_MACHINE_ID,
    work_class: StartRequest['work_class'] = 'worker',
  ): StartRequest => ({ identity, destination, work_class });
  const home = { conversation_id: 'admission-home', turn_number: 1 };
  const away = { conversation_id: 'admission-away', turn_number: 3 };
  const child = {
    conversation_id: away.conversation_id,
    executor_incarnation: 'executor',
    child_id: 'current',
  };
  const checkpoints: unknown[] = [];
  const capture = (admission: AutomaticStartAdmission) =>
    checkpoints.push({
      snapshots: [...admission.snapshots()].sort((a, b) =>
        a.request.identity.localeCompare(b.request.identity),
      ),
      rows: db.read((sql) => sql.all('SELECT * FROM automatic_start_bindings ORDER BY identity')),
    });
  try {
    const first = make();
    const grants = [
      first.try_start(request('h1')),
      first.try_start(request('h2')),
      first.try_start(request('away', ORIGINAL_MAC_MACHINE_ID)),
    ];
    now = 1;
    grants.push(first.try_start(request('h2')));
    now = 2;
    grants.push(first.try_start(request('h3')));
    now = 3;
    grants.push(
      first.try_start(request('h4')),
      first.try_start(request('chief', HOME_MACHINE_ID, 'supervisor')),
    );
    first.bind(['h1'], home);
    first.bind(['away'], away);
    first.persist_bindings(db, home);
    first.persist_bindings(db, away);
    first.bind_child(db, away, child);
    first.persist_bindings(db, { ...away });
    capture(first);
    const restored = make();
    db.read((sql) => {
      restored.restore_bindings(sql, { ...home });
      restored.restore_bindings(sql, { ...away });
    });
    capture(restored);
    grants.push(restored.allow_dispatch({ ...home }), restored.allow_dispatch({ ...away }));
    restored.bind_source({ ...away }, {});
    restored.failed_attempt(db, { ...away });
    restored.accept(db, { ...home, turn_number: 2 });
    restored.retire_remote(db, { ...child, child_id: 'previous' });
    capture(restored);
    restored.accept(db, { ...home });
    restored.retire_remote(db, { ...child });
    capture(restored);
    return { grants, checkpoints };
  } finally {
    db.close();
  }
}
