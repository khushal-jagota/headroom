/** Existing Python installed-home fixtures drive real service and installer state machines. */
import assert from 'node:assert/strict';
import {
  copyFileSync,
  existsSync,
  readFileSync,
  realpathSync,
  unlinkSync,
  writeFileSync,
} from 'node:fs';
import { basename, join } from 'node:path';
import { ChildProcesses } from '../../../../lifecycle/owned_processes.ts';
import { install } from '../../installer/install.ts';
import type { InstallerEffects, InstallerRequest } from '../../installer/contracts.ts';
import {
  create_darwin_home_io,
  create_darwin_installer_effects,
  DarwinHomeService,
} from '../service.ts';
import type { DarwinHomeJob, DarwinHomeProcess } from '../contracts.ts';

const LABEL = 'com.panels.proof',
  FROM = 'a'.repeat(40),
  TO = 'b'.repeat(40);
interface Case {
  root: string;
  scenario: string;
  jobs: Record<string, DarwinHomeJob>;
}
export async function darwin_updates(input: { cases: Case[]; account_home: string }) {
  const outcomes = [],
    records = [];
  let v2_identity: unknown;
  for (const spec of input.cases) {
    const { root, scenario } = spec;
    const events: string[][] = [];
    const jobs = new Map(
      ['', '.backup', '.maintenance', '.stall-watchdog'].map((suffix, index) => [
        LABEL + suffix,
        [123, 125, 126, 127][index]!,
      ]),
    );
    const inventory = new Map<number, DarwinHomeProcess>(
      [...jobs.values()].map((pid) => [
        pid,
        { pid, parent: 1, uid: process.getuid!(), birth: `1:${pid}` },
      ]),
    );
    if (scenario === 'rollback')
      inventory.set(124, { pid: 124, parent: 126, uid: process.getuid!(), birth: '1:124' });
    let retired_child = false,
      child_reads = 0,
      generation = 1,
      foreign = false;
    const processes = new ChildProcesses();
    const execution = {
      child_processes: processes,
      signal: AbortSignal.timeout(20_000),
      deadline: performance.now() / 1000 + 20,
    };
    const decoded = (path: string): DarwinHomeJob => {
      const bytes = readFileSync(path, 'utf8');
      return bytes.trimStart().startsWith('{')
        ? (JSON.parse(bytes) as DarwinHomeJob)
        : spec.jobs[basename(path, '.plist')]!;
    };
    const io = create_darwin_home_io(
      execution,
      { HOME: input.account_home },
      {
        process: (pid) => {
          if (pid === 124 && retired_child) {
            child_reads++;
            return {
              pid,
              parent: 1,
              uid: process.getuid!() + 1,
              birth: `${child_reads === 1 ? 1 : 2}:124`,
            };
          }
          return inventory.get(pid) ?? null;
        },
        processes: () => new Map(inventory),
        async command(argv) {
          let code = 0,
            stdout = '',
            stderr = '';
          if (argv[0] === '/usr/bin/plutil') {
            const path = argv.at(-1)!;
            stdout = JSON.stringify(decoded(path));
          } else {
            assert.equal(argv[0], '/bin/launchctl');
            const operation = argv[1]!,
              label = argv.at(-1)!.split('/').at(-1)!;
            if (operation === 'print') {
              const pid = jobs.get(label);
              if (pid === undefined) {
                code = 1;
                stderr = 'Could not find service';
              } else {
                const path = join(root, 'agents', label + '.plist'),
                  value = decoded(path);
                stdout = `path = ${path}\nprogram = ${value.ProgramArguments[0]}\nworking directory = ${root}\npid = ${pid}\narguments = {\n${value.ProgramArguments.join('\n')}\n}\nenvironment = {\n${Object.entries(
                  value.EnvironmentVariables,
                )
                  .map(([key, value]) => `${key} => ${value}`)
                  .join('\n')}\n}\n`;
                if (foreign && label === LABEL)
                  stdout = stdout.replaceAll(join(root, 'app/bin/panels'), '/foreign/panels');
              }
            } else if (operation === 'bootout') {
              events.push([operation, label]);
              if (scenario === 'partial' && label === LABEL) throw new Error('owned stop refused');
              inventory.delete(jobs.get(label)!);
              jobs.delete(label);
              if (inventory.has(124) && label.endsWith('.maintenance')) {
                retired_child = true;
                inventory.delete(124);
              }
            } else if (operation === 'bootstrap') {
              const label = basename(argv.at(-1)!, '.plist');
              events.push([operation, label]);
              if (scenario === 'auxiliary' && label.endsWith('.backup'))
                throw new Error('auxiliary refused');
              const pid = new Map([
                ['', 123],
                ['.backup', 125],
                ['.maintenance', 126],
                ['.stall-watchdog', 127],
              ]).get(label.slice(LABEL.length))!;
              jobs.set(label, pid);
              inventory.set(pid, {
                pid,
                parent: 1,
                uid: process.getuid!(),
                birth: `${++generation}:${pid}`,
              });
              if (
                label === LABEL &&
                JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8')).app_sha === TO
              )
                writeFileSync(join(root, 'data/planner.db'), 'candidate committed work');
            } else throw new Error('Unexpected fixture command ' + argv.join(' '));
          }
          return { code, stdout, stderr };
        },
      },
    );
    const service = new DarwinHomeService(root, `gui/${io.uid}/${LABEL}`, process.execPath, io);
    try {
      if (scenario === 'success') {
        foreign = true;
        await assert.rejects(service.stop());
        assert.equal(events.length, 0, 'foreign job authentication must precede every bootout');
        foreign = false;
        for (const suffix of ['.maintenance', '.stall-watchdog']) {
          unlinkSync(join(root, 'agents', LABEL + suffix + '.plist'));
          inventory.delete(jobs.get(LABEL + suffix)!);
          jobs.delete(LABEL + suffix);
        }
        const identity_path = join(root, 'data/mac-home-installation.json'),
          legacy = readFileSync(identity_path);
        const value = JSON.parse(legacy.toString('utf8'));
        delete value.installer_python;
        Object.assign(value, { version: 2, installer_node: realpathSync(process.execPath) });
        writeFileSync(identity_path, JSON.stringify(value));
        v2_identity = new DarwinHomeService(root, `gui/${io.uid}/${LABEL}`, process.execPath, io)
          .identity;
        writeFileSync(identity_path, legacy);
      }
      const health = async (sha: string) => {
        events.push([
          'health',
          sha,
          existsSync(join(root, 'data/update-barrier.json')) ? 'closed' : 'open',
        ]);
        return scenario !== 'rollback' || sha === FROM;
      };
      const effects: InstallerEffects = {
        ...create_darwin_installer_effects(async () => service, health),
        async capture() {
          events.push(['capture']);
          return { kind: 'returned', returncode: 0 };
        },
        async restore(snapshot, descriptor) {
          assert.equal(typeof descriptor, 'number');
          events.push(['restore']);
          copyFileSync(join(snapshot, 'database.sqlite'), join(root, 'data/planner.db'));
        },
        healthy: health,
      };
      const request: InstallerRequest = {
        current_root: root,
        data_directory: join(root, 'data'),
        database: join(root, 'data/planner.db'),
        attempt_id: 'proof',
        service_unit: `gui/${io.uid}/${LABEL}`,
        health_url: 'http://127.0.0.1:0',
        health_timeout_seconds: 1,
        node_executable: process.execPath,
        admitted_legacy_null_sha: null,
      };
      const code = await install(request, effects, { stderr() {} }, execution.signal);
      const record = JSON.parse(readFileSync(join(root, 'data/update-attempt.json'), 'utf8'));
      records.push(record);
      outcomes.push({
        code,
        events,
        state: record.state,
        steps: Object.keys(record.steps).sort(),
        detail_present: Boolean(record.detail),
        app: JSON.parse(readFileSync(join(root, 'app/manifest.json'), 'utf8')).app_sha,
        staged: existsSync(join(root, '.app-next-' + TO)),
        previous: existsSync(join(root, '.app-previous-' + FROM)),
        database: readFileSync(join(root, 'data/planner.db'), 'utf8'),
        barrier: existsSync(join(root, 'data/update-barrier.json')),
        jobs: [...jobs.keys()].sort(),
        retired_birth_checked: child_reads >= 2,
      });
    } finally {
      processes.seal();
      await processes.settled();
    }
  }
  return { outcomes, records, v2_identity };
}
