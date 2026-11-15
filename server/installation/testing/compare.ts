/** Populated orchestration witness. No service, provider, or candidate code executes. */
import { existsSync, mkdirSync, readFileSync, readdirSync, readlinkSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { ChildProcesses } from '../../lifecycle/owned_processes.ts';
import type { AppManifest } from '../../environments/release/contracts.ts';
import { install_home } from '../home.ts';
import { create_linux_home_service } from '../linux.ts';
import { create_darwin_home_service } from '../darwin.ts';
import type { HomeInstallDependencies, HomeInstallRequest } from '../contracts.ts';

export interface HomeComparisonCase { readonly root: string; readonly system: 'linux' | 'darwin'; readonly scenario: string; readonly port: number }
function normalized_path(path: string, root: string, system: string): string {
  return path.split(':').filter((entry) => entry !== join(root, 'app/.venv/bin') && (system !== 'linux' || entry !== dirname(process.execPath))).join(':');
}
function environment_shape(environment: Record<string, string>, root: string, system: string): Record<string, string> {
  return Object.fromEntries(Object.entries(environment).filter(([key]) => key !== 'PYTHONDONTWRITEBYTECODE').map(([key, value]) => [key, key === 'PATH' ? normalized_path(value, root, system) : value]));
}
export async function home_install_comparison(cases: readonly HomeComparisonCase[]): Promise<unknown[]> {
  const outputs: unknown[] = [];
  for (const value of cases) {
    const base = value.root, account = join(base, 'account'), root = join(base, 'home'), service = 'com.panels.proof';
    const agents = join(account, value.system === 'darwin' ? 'Library/LaunchAgents' : '.config/systemd/user');
    const active = new Set<string>(), jobs = new Map<string, string>(), effects: string[][] = [];
    const owner = new ChildProcesses(), controller = new AbortController();
    const request: HomeInstallRequest = { root, service, port: value.port, installer_python: process.execPath, isolated_providers: value.scenario !== 'configured', archive: join(base, 'archive'), archive_sha256: 'a'.repeat(64), revision: 'b'.repeat(40) };
    const environment = { HOME: account, PATH: join(base, 'provider/bin') + ':/build-icu/bin:/usr/bin:/bin', CODEX_HOME: join(base, 'configured-codex'), CLAUDE_CONFIG_DIR: join(base, 'configured-claude'), PLAN_HERMES_HOME: join(base, 'configured-hermes'), PLAN_CLAUDE_EXECUTABLE: '/custom/claude' };
    const deps: HomeInstallDependencies = {
      system: value.system, architecture: value.system === 'darwin' ? 'arm64' : 'x86_64', account_home: account, uid: process.getuid!(), environment,
      execution: { child_processes: owner, signal: controller.signal, deadline: null },
      async extract(_archive, destination) {
        mkdirSync(join(destination, 'bin'), { recursive: true });
        writeFileSync(join(destination, 'bin/panels'), 'sealed application');
        mkdirSync(join(destination, 'src/planner/skills/panels'), { recursive: true });
        writeFileSync(join(destination, 'src/planner/skills/panels/SKILL.md'), 'Panels');
        return { runtime_contract: { version: 2 } } as AppManifest;
      },
      async admit() {},
      async healthy() { effects.push(['health']); return value.scenario !== 'health-failure' && value.scenario !== 'stop-unconfirmed'; },
      service: (options, host) => value.system === 'darwin' ? create_darwin_home_service(options, host) : create_linux_home_service(options, host),
      warning() {},
      async command(argv, _execution, _timeout, allow_nonzero) {
        let stdout = '', stderr = '', code = 0;
        if (argv[0] === '/usr/bin/plutil') return { code, stdout: readFileSync(argv.at(-1)!, 'utf8'), stderr };
        if (argv[0] === 'loginctl') stdout = 'yes\n';
        else if (argv[0] === 'systemctl') {
          const operation = argv[2]!, name = argv[3]!;
          if (operation === 'show') {
            if (argv.includes('--property=FragmentPath')) stdout = join(agents, name);
            else stdout = value.scenario === 'loaded-conflict' && name === service + '.service' ? 'loaded' : 'not-found';
          } else if (operation === 'is-active') stdout = active.has(name) ? 'active' : 'inactive';
          else if (operation !== 'show-environment') {
            effects.push([...argv]);
            if (operation === 'enable') {
              active.add(argv.at(-1)!);
              if (value.scenario === 'auxiliary-failure' && argv.at(-1)!.endsWith('.timer')) code = 1;
            } else if (operation === 'stop' && value.scenario !== 'stop-unconfirmed') active.delete(name);
          }
        } else if (argv[0] === '/bin/launchctl') {
          const operation = argv[1]!, label = argv.at(-1)!.split('/').at(-1)!;
          if (operation === 'print') {
            if (value.scenario === 'loaded-conflict' && label === service) stdout = 'path = /another/home.plist\n';
            else if (jobs.has(label)) stdout = `path = ${jobs.get(label)}\n`;
            else { code = 1; stderr = 'Could not find service'; }
          } else {
            effects.push([...argv]);
            if (operation === 'bootstrap') {
              const path = argv.at(-1)!, name = path.split('/').at(-1)!.slice(0, -6);
              jobs.set(name, path);
              if (value.scenario === 'auxiliary-failure' && name.endsWith('.backup')) code = 1;
            } else if (operation === 'bootout' && value.scenario !== 'stop-unconfirmed') jobs.delete(label);
          }
        } else throw new Error(`Unadmitted comparison command: ${argv[0]}`);
        if (code !== 0 && !allow_nonzero) throw new Error('scripted command refusal');
        return { stdout, stderr, code };
      },
    };
    let outcome = 'ok';
    try { await install_home(request, deps); }
    catch { outcome = 'refused'; }
    finally { owner.seal(); await owner.settled(); }
    let generated: unknown = null;
    if (existsSync(join(root, 'data/config.yaml'))) {
      if (value.system === 'darwin') {
        generated = Object.fromEntries(readdirSync(join(root, 'agents')).sort().map((name) => {
          const job = JSON.parse(readFileSync(join(root, 'agents', name), 'utf8'));
          job.EnvironmentVariables = environment_shape(job.EnvironmentVariables, root, value.system);
          return [name, job];
        }));
      } else {
        generated = Object.fromEntries(readdirSync(agents).filter((name) => name !== 'foreign.service').sort().map((name) => [name, readFileSync(join(agents, name), 'utf8').split('\n').filter((line) => !line.startsWith('Environment="PYTHONDONTWRITEBYTECODE=')).map((line) => line.startsWith('Environment="PATH=') ? 'Environment=' + JSON.stringify('PATH=' + normalized_path(JSON.parse(line.slice('Environment='.length)).slice('PATH='.length), root, value.system)) : line).sort().join('\n')]));
      }
    }
    outputs.push(JSON.parse(JSON.stringify({ outcome, root_exists: existsSync(root), config: existsSync(join(root, 'data/config.yaml')) ? JSON.parse(readFileSync(join(root, 'data/config.yaml'), 'utf8')) : null, application: existsSync(join(root, 'app/bin/panels')) ? readFileSync(join(root, 'app/bin/panels'), 'utf8') : null, generated, owned_files: readdirSync(agents).filter((name) => !name.startsWith('foreign.')).sort(), active: value.system === 'darwin' ? [...jobs.keys()].sort() : [...active].sort(), effects, foreign: readFileSync(join(agents, value.system === 'darwin' ? 'foreign.plist' : 'foreign.service'), 'utf8'), foreign_skill: readlinkSync(join(base, 'configured-codex/skills/panels')) }).replaceAll(base, '<root>')));
  }
  return outputs;
}
