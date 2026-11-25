/** Execute the normal owner action against the previously installed home and its existing machine. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { mkdir, readFile, rename, rm, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import { recorded_failure_detail } from '../../../conversation/logic/backend_events.ts';
import { chromium } from 'playwright';
import {
  check_large_conversation,
  archive_histories,
  seed_active_histories,
  check_committed_views,
} from './large_conversation.ts';
import { published_mac_home_fault } from './mac_home_machine_fault.ts';
import { UPDATE_HEALTH_TIMEOUT_SECONDS } from '../../updates/configuration.ts';
import { healthy } from '../../updates/installer/health.ts';
import { HOME_HEALTH_TIMEOUT_SECONDS } from '../../../installation/configuration.ts';
import { HOME_MACHINE_ID } from '../../../execution_identity.ts';
import type { MachineUpdateState } from '../../updates/view_contracts.ts';
import { smoke_file_turn, verify_smoke_history } from '../../../tools/smoke/steps.ts';
import { DEFAULT_BACKEND_PEER_REPLY } from '../../../tools/smoke/codex_peer.ts';
import { credential_path } from '../../../executor/credential_file.ts';
import { MACHINE_SERVICE_TRIAL_FILE } from '../../../executor/mac/configuration.ts';
import { mac_installation_paths } from '../../../executor/mac/platform.ts';
import { LINUX_EXECUTOR_SERVICE_NAME } from '../../linux_executor/configuration.ts';
import { launchd_field } from '../../updates/darwin/logic/launchd.ts';

interface Options {
  base: string;
  port: number;
  service: string;
  machine_id: string;
  joined_state: string;
  predecessor: string;
  verify_rollback?(): Promise<void>;
}

async function wait_for(read: () => Promise<boolean>, description: string, poll_ms = 500) {
  const until = performance.now() + 600_000;
  while (performance.now() < until) {
    if (await read()) return;
    await delay(poll_ms);
  }
  throw new Error('Timed out ' + description);
}

async function machine_service_snapshot(label: string) {
  const execute = promisify(execFile);
  if (process.platform === 'linux') {
    const { stdout } = await execute('/usr/bin/systemctl', [
      '--user',
      'show',
      label,
      '-p',
      'MainPID',
      '-p',
      'FragmentPath',
      '-p',
      'ExecStart',
    ]);
    const fields = new Map(
      stdout
        .trim()
        .split('\n')
        .map((line) => {
          const index = line.indexOf('=');
          return [line.slice(0, index), line.slice(index + 1)];
        }),
    );
    return {
      pid: Number(fields.get('MainPID')),
      definition: await readFile(fields.get('FragmentPath')!, 'utf8'),
      command: fields.get('ExecStart')!,
    };
  }
  const { stdout } = await execute('/bin/launchctl', [
    'print',
    'gui/' + process.getuid!() + '/' + label,
  ]);
  return {
    pid: Number(launchd_field(stdout, 'pid')),
    definition: await readFile(launchd_field(stdout, 'path')!, 'utf8'),
    command: stdout,
  };
}

export async function qualify_installed_upgrade(o: Options): Promise<void> {
  const url = `http://127.0.0.1:${o.port}`;
  const candidate = (await readFile(join(o.base, 'candidate.sha'), 'utf8')).trim();
  assert.notEqual(candidate, o.predecessor);
  const credential = await readFile(credential_path(o.joined_state));
  const read_status = async () => {
    try {
      const response = await fetch(url + '/api/updates', { signal: AbortSignal.timeout(3000) });
      return response.ok ? await response.json() : null;
    } catch {
      return null; // The listener disappears during the restart.
    }
  };
  assert.equal((await read_status())?.app_sha, o.predecessor);
  const workspace = join(o.base, 'conversation-workspace');
  await mkdir(workspace, { mode: 0o700 });
  const missingMachine = await published_mac_home_fault(o.base, o.service, o.predecessor);
  const turn = (after_rollback = false, worker_artifact?: 'before-update' | 'after-rollback') =>
    smoke_file_turn(
      url,
      {
        model: 'TEST',
        reply: DEFAULT_BACKEND_PEER_REPLY,
        workspace,
        after_rollback,
        worker_artifact,
      },
      o.machine_id,
    );
  const before = await turn(false, missingMachine ? 'before-update' : undefined);
  await promisify(execFile)('python3', [
    'server/environments/release/testing/configure_feed.py',
    o.base,
    (await readFile(join(o.base, 'feed/port'), 'utf8')).trim(),
    o.service,
  ]);
  await wait_for(
    async () => (await read_status())?.available?.app_sha === candidate,
    'waiting for predecessor discovery',
  );
  const readJournal = async () =>
    JSON.parse(await readFile(join(o.base, 'installed/data/update-attempt.json'), 'utf8'));
  const browser = await chromium.launch();
  const qualify_service = process.env.QUALIFY_MACHINE_SERVICE === '1';
  const joined_label =
    process.platform === 'linux'
      ? LINUX_EXECUTOR_SERVICE_NAME
      : mac_installation_paths(process.env.HOME!).job;
  const joined_updates = join(o.joined_state, 'updates');
  const ready = join(joined_updates, 'ready');
  const retained_ready = ready + '.qualification';
  let readiness_blocked = false;
  let phase = 'installing and rolling back';
  try {
    const page = await browser.newPage();
    await page.goto(url);
    // Other journeys make the real candidate fail to bind on this disposable port.
    const fault = createServer((_request, response) => {
      response.writeHead(503, { Connection: 'close' });
      response.end();
    });
    let failedAttemptId: string;
    let refusedHealth: { ready: false; error: string } | undefined;
    try {
      await missingMachine?.stop();
      const started = page.waitForResponse(
        (response) =>
          response.request().method() === 'POST' &&
          new URL(response.url()).pathname === '/api/updates/install',
      );
      await page
        .locator('[data-shell-status]')
        .getByRole('button', { name: 'Update', exact: true })
        .click();
      const confirm = page.getByRole('dialog').getByRole('button', { name: 'Update', exact: true });
      if (
        (await Promise.race([
          confirm.waitFor().then(() => 'dialog'),
          started.then(() => 'started'),
        ])) === 'dialog'
      )
        await confirm.click();
      const accepted = await started;
      assert.equal(accepted.ok(), true, await accepted.text());
      const firstAttempt = (await accepted.json()).attempt;
      assert.equal(firstAttempt.to_sha, candidate);
      failedAttemptId = firstAttempt.attempt_id;
      assert.ok(failedAttemptId);
      const activeFaultAttempt = async () => {
        const attempt = await readJournal();
        assert.equal(attempt.attempt_id, failedAttemptId);
        assert.equal(attempt.to_sha, candidate);
        assert.ok(
          attempt.state !== 'failed' && attempt.state !== 'done' && !attempt.steps?.rollback_failed,
          'Installer ended during fault injection: ' +
            (recorded_failure_detail(attempt.detail) ?? attempt.state),
        );
        return attempt;
      };
      if (missingMachine) {
        await wait_for(async () => {
          const attempt = await activeFaultAttempt();
          if (!attempt.steps?.app_swapped) return false;
          const response = await fetch(url + '/api/health?expected_sha=' + candidate, {
            signal: AbortSignal.timeout(1_000),
          }).catch(() => null);
          if (!response) return false;
          const health = await response.json();
          assert.ok(!response.ok, 'Candidate reported ready without its enrolled home machine');
          if (
            response.status !== 503 ||
            health.ready !== false ||
            health.error !== 'Waiting for the enrolled home machine'
          )
            return false;
          refusedHealth = { ready: false, error: health.error };
          return true;
        }, 'waiting for the candidate to report its missing enrolled home machine');
      } else
        await wait_for(
          async () => {
            await activeFaultAttempt();
            return new Promise<boolean>((done, reject) => {
              const listening = () => {
                fault.off('error', busy);
                done(true);
              };
              const busy = (error: NodeJS.ErrnoException) => {
                fault.off('listening', listening);
                if (error.code === 'EADDRINUSE') done(false);
                else reject(error);
              };
              fault.once('error', busy);
              fault.once('listening', listening);
              fault.listen(o.port, '127.0.0.1');
            });
          },
          'injecting failure on candidate bind',
          20,
        );
      await wait_for(
        async () => {
          const attempt = await activeFaultAttempt();
          if (!attempt.steps?.rollback_service_stopped) return false;
          assert.ok(
            attempt.steps.app_swapped,
            'The failing transaction must have selected the candidate',
          );
          return true;
        },
        'waiting for the installer to stop the failed candidate',
        20,
      );
    } finally {
      await missingMachine?.restore();
      if (fault.listening) await new Promise<void>((done) => fault.close(() => done()));
    }
    const rollbackEvidence = join(o.base, 'evidence/rollback.json');
    await wait_for(async () => {
      const attempt = await readJournal();
      assert.equal(attempt.attempt_id, failedAttemptId);
      assert.equal(attempt.from_sha, o.predecessor);
      assert.equal(attempt.to_sha, candidate);
      if (attempt.state === 'failed' || attempt.steps?.rollback_failed) {
        const detail = recorded_failure_detail(attempt.detail);
        const backup_present = typeof attempt.backup === 'string' && attempt.backup.length > 0;
        const status = await read_status();
        await writeFile(
          rollbackEvidence,
          JSON.stringify(
            {
              fault: missingMachine ? 'home machine offline' : 'candidate port occupied',
              app_swapped: attempt.steps?.app_swapped ?? null,
              rollback_app_restored: attempt.steps?.rollback_app_restored ?? null,
              candidate_health_503: refusedHealth,
              runtime: status?.app_sha ?? null,
              attempt_id: failedAttemptId,
              outcome: attempt.state,
              refused_candidate: candidate,
              detail,
              snapshot_required: attempt.snapshot_required,
              backup_present,
              rollback_database_restored: attempt.steps?.rollback_database_restored ?? null,
              rollback_healthy: attempt.steps?.rollback_healthy ?? null,
              rollback_failed: attempt.steps?.rollback_failed ?? null,
              enrolled_machine: o.machine_id,
              preserved_file: before.file_url,
              preserved_attachment: before.stored_file_id,
            },
            null,
            2,
          ),
        );
        assert.ok(
          !attempt.steps?.rollback_failed &&
            attempt.steps?.rollback_app_restored &&
            attempt.steps?.rollback_healthy,
          'Installer did not recover the predecessor: ' + detail,
        );
        assert.ok(!attempt.snapshot_required || backup_present, 'Required snapshot missing');
        assert.ok(
          !backup_present || attempt.steps.rollback_database_restored,
          'Installer did not restore the database snapshot',
        );
        return status?.app_sha === o.predecessor && status.attempt?.attempt_id === failedAttemptId;
      }
      return false;
    }, 'waiting for the terminal rollback journal and restored predecessor');
    if (missingMachine)
      await wait_for(missingMachine.reconnected, 'waiting for the restored home machine lease');
    await o.verify_rollback?.();
    assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
    const preserved = { ...before, model: 'TEST' };
    await verify_smoke_history(url, preserved);
    const recovered = await turn(true, missingMachine ? 'after-rollback' : undefined);
    const expectedBuild = JSON.parse(await readFile(join(o.base, 'feed/publication.json'), 'utf8'))
      .parts.machines[process.platform === 'darwin' ? 'darwin-arm64' : 'linux-x86_64'].build_id;
    assert.match(expectedBuild, /^[a-f0-9]{64}$/);
    const rolledBackAttempt = failedAttemptId;
    await page.goto(url);
    const card = page.locator('[data-shell-status]:visible');
    const headline = card.locator('.status-base');
    await headline.getByText("Update didn't install", { exact: true }).waitFor();
    await page.screenshot({
      path: join(o.base, 'evidence/update-rolled-back.png'),
      animations: 'disabled',
    });
    phase = 'seeding active conversation views before the upgrade';
    const histories = await seed_active_histories(
      o.base,
      url,
      recovered.conversation_id,
      o.machine_id,
      workspace,
    );
    phase = 'retrying the update';
    const predecessor_service = qualify_service
      ? await machine_service_snapshot(joined_label)
      : null;
    if (predecessor_service) {
      assert.ok(predecessor_service.pid > 0);
      // Only this machine loses the ability to persist its trial readiness.
      // Its old version can lease and run ordinary work without a trial marker.
      await mkdir(ready, { recursive: true });
      await rename(ready, retained_ready);
      readiness_blocked = true;
      await writeFile(ready, 'Withhold joined machine trial confirmation.\n', { flag: 'wx' });
    }
    const retryStarted = performance.now();
    const retried = page.waitForResponse(
      (response) =>
        response.request().method() === 'POST' &&
        new URL(response.url()).pathname ===
          `/api/updates/attempts/${encodeURIComponent(rolledBackAttempt)}/retry`,
    );
    await headline.getByRole('button', { name: 'Try again', exact: true }).click();
    const retryResponse = await retried;
    assert.ok(retryResponse.ok(), await retryResponse.text());
    phase = 'checking the release feed, home and enrolled machines';
    const enrolled = [HOME_MACHINE_ID, o.machine_id];
    let trialTiming;
    let machine_service_evidence:
      | {
          predecessor_pid: number;
          rejected_candidate_pid: number;
          restored_pid: number;
          restored_turn: string;
        }
      | undefined;
    let candidate_trial: Awaited<ReturnType<typeof machine_service_snapshot>> | undefined;
    let first_views: Awaited<ReturnType<typeof check_committed_views>> | undefined;
    await wait_for(async () => {
      const journal = await readJournal();
      if (journal.attempt_id === rolledBackAttempt) return false;
      if (journal.state === 'failed' || journal.steps?.rollback_failed)
        throw new Error('Installer retry failed: ' + recorded_failure_detail(journal.detail));
      if (journal.state === 'done' && !first_views) {
        const started = Date.parse(journal.steps.service_started);
        const healthy = Date.parse(journal.steps.healthy);
        assert.ok(Number.isFinite(started) && healthy >= started);
        trialTiming = {
          press_to_commit_seconds: (performance.now() - retryStarted) / 1000,
          service_started_to_healthy_seconds: (healthy - started) / 1000,
          health_deadline_seconds: UPDATE_HEALTH_TIMEOUT_SECONDS,
        };
        first_views = await check_committed_views(url, histories);
      }
      const status = await read_status();
      if (status?.attempt?.attempt_id !== journal.attempt_id) return false;
      if (status?.attempt?.state === 'failed')
        throw new Error(recorded_failure_detail(status.attempt.detail) ?? 'Installer retry failed');
      if (predecessor_service && !machine_service_evidence) {
        const joined = status?.machines.find(
          (m: MachineUpdateState) => m.machine_id === o.machine_id,
        );
        if (joined?.state !== 'failed' || !joined.present || !joined.can_retry) {
          const running = await machine_service_snapshot(joined_label).catch(() => null);
          if (
            running &&
            running.pid > 0 &&
            running.command.includes('updates/releases/' + candidate)
          )
            candidate_trial = running;
          return false;
        }
        await wait_for(
          async () =>
            readFile(join(joined_updates, MACHINE_SERVICE_TRIAL_FILE)).then(
              () => false,
              (error) => {
                if (error.code === 'ENOENT') return true;
                throw error;
              },
            ),
          'waiting for the failed machine service trial to settle',
        );
        const restored = await machine_service_snapshot(joined_label);
        assert.ok(candidate_trial, 'The replacement machine service never started');
        assert.equal(restored.definition, predecessor_service.definition);
        assert.ok(restored.pid > 0 && restored.pid !== candidate_trial.pid);
        await o.verify_rollback?.();
        const restored_turn = await turn(true);
        assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
        await rm(ready);
        await rename(retained_ready, ready);
        readiness_blocked = false;
        const retried = await fetch(
          url +
            '/api/updates/attempts/' +
            encodeURIComponent(joined.attempt_id) +
            '/machines/' +
            encodeURIComponent(o.machine_id) +
            '/retry',
          { method: 'POST' },
        );
        assert.ok(retried.ok, await retried.text());
        machine_service_evidence = {
          predecessor_pid: predecessor_service.pid,
          rejected_candidate_pid: candidate_trial.pid,
          restored_pid: restored.pid,
          restored_turn: restored_turn.conversation_id,
        };
        return false;
      }
      return (
        status?.app_sha === candidate &&
        status.attempt?.state === 'done' &&
        status.attempt.to_sha === candidate &&
        status.state === 'up_to_date' &&
        enrolled.every((id) =>
          status.machines.some(
            (m: MachineUpdateState) =>
              m.machine_id === id &&
              m.present &&
              m.state === 'current' &&
              m.build_id === expectedBuild,
          ),
        )
      );
    }, 'waiting for the checked release and both enrolled machines to run the committed candidate');
    assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
    if (machine_service_evidence) {
      const home_label =
        o.service + (process.platform === 'linux' ? '.machine.service' : '.machine');
      for (const label of [joined_label, home_label]) {
        const service = await machine_service_snapshot(label);
        assert.ok(service.pid > 0);
        assert.ok(service.command.includes('updates/releases/' + candidate));
      }
      await writeFile(
        join(o.base, 'evidence/machine-service.json'),
        JSON.stringify(machine_service_evidence, null, 2),
      );
    }
    const requesters = join(o.base, 'evidence/mac-worker-requesters.jsonl');
    const check_requester = !!machine_service_evidence && process.platform === 'darwin';
    if (check_requester) await rm(requesters, { force: true });
    const after = await turn();
    if (check_requester) {
      const launcher = mac_installation_paths(process.env.HOME!).app_executable;
      const observed = (await readFile(requesters, 'utf8'))
        .trim()
        .split('\n')
        .map((line) => JSON.parse(line));
      assert.ok(
        observed.some((requester) => requester.executable === launcher),
        'The installed signed Mac app did not start the qualified Worker',
      );
    }
    assert.notEqual(before.conversation_id, after.conversation_id);
    await verify_smoke_history(url, preserved);
    phase = 'reopening the update screen';
    const rail = page.getByRole('region', { name: 'Workspace', exact: true });
    const reopen = async (alreadyQuiet = false) => {
      const [response] = await Promise.all([
        page.waitForResponse(
          (response) => new URL(response.url()).pathname === '/api/updates' && response.ok(),
        ),
        page.reload(),
      ]);
      const settled = await response.json();
      assert.equal(settled.state, 'up_to_date');
      assert.equal(settled.attempt?.to_sha, candidate);
      assert.ok(Date.parse(settled.attempt.completed_at), 'Expected the saved completion time');
      const selector = `[data-shell-status][data-update-attempt="${settled.attempt.attempt_id}"]`;
      const slot = rail.locator(selector);
      await slot.waitFor({ state: 'attached' });
      if (alreadyQuiet)
        assert.equal(await slot.locator('[data-status-state="machines"]').count(), 0);
      assert.equal(
        await slot.getByText('This update will interrupt running agents', { exact: true }).count(),
        0,
      );
      await rail.locator(selector + '[aria-hidden="true"]').waitFor({ state: 'attached' });
      await slot.locator('.status-card').waitFor({ state: 'detached' });
    };
    await reopen();
    await reopen(true); // A second visit must not revive a completed update.
    await rail.screenshot({
      path: join(o.base, 'evidence/sidebar-after-update.png'),
      animations: 'disabled',
    });
    await page.screenshot({
      path: join(o.base, 'evidence/update-complete.png'),
      animations: 'disabled',
    });
    let originalStart:
      | { entry: string; feed_entry: string; running_sha: string; conversation_id: string }
      | undefined;
    if (process.platform === 'linux') {
      phase = 'restoring the original Linux service';
      await promisify(execFile)('python3', [
        'server/environments/release/testing/configure_feed.py',
        o.base,
        '--restore',
        o.service,
      ]);
      assert.ok(
        await healthy(
          url + '/api/health',
          candidate,
          HOME_HEALTH_TIMEOUT_SECONDS,
          new AbortController().signal,
          process.env,
          true,
        ),
        'Original installed Linux launcher did not start the ready candidate',
      );
      const restarted = await turn();
      originalStart = {
        entry: 'app/bin/panels-launcher serve',
        feed_entry: 'app/bin/panels serve',
        running_sha: candidate,
        conversation_id: restarted.conversation_id,
      };
    }
    phase = 'opening active conversations and receiving their new messages';
    assert.ok(first_views);
    const large_conversations = [];
    for (const history of histories)
      large_conversations.push(await check_large_conversation(page, url, o.base, history));
    await writeFile(
      join(o.base, 'evidence/upgrade.json'),
      JSON.stringify(
        {
          predecessor: o.predecessor,
          candidate,
          machine_id: o.machine_id,
          reported_build: expectedBuild,
          baseline_file_check: missingMachine
            ? 'joined text turn and Worker-written Ticket artifact'
            : 'file attachment delivered to the joined agent',
          preserved_file: before.file_url,
          candidate_agent_attachment: after.stored_file_id,
          conversations: [before.conversation_id, after.conversation_id],
          rollback_sha: o.predecessor,
          restored_home_machine: missingMachine?.label,
          recovered_conversation: recovered.conversation_id,
          original_linux_start: originalStart,
          trial_timing: trialTiming,
          first_views,
          archive_histories,
          large_conversations,
          publication: JSON.parse(await readFile(join(o.base, 'feed/publication.json'), 'utf8')),
        },
        null,
        2,
      ),
    );
  } catch (error) {
    const page = browser.contexts()[0]?.pages()[0];
    const status = await read_status();
    const cards = await page
      ?.locator('[data-shell-status]:visible')
      .allInnerTexts()
      .catch(() => []);
    const journal = await readJournal().catch(() => null);
    const evidence = {
      phase,
      journal: journal && {
        attempt_id: journal.attempt_id,
        from_sha: journal.from_sha,
        to_sha: journal.to_sha,
        state: journal.state,
        steps: Object.keys(journal.steps ?? {}),
        detail: recorded_failure_detail(journal.detail),
      },
      card_text: cards?.map(recorded_failure_detail),
      app_sha: status?.app_sha,
      state: status?.state,
      attempt: status?.attempt && {
        state: status.attempt.state,
        target: status.attempt.to_sha,
        detail: recorded_failure_detail(status.attempt.detail),
      },
      machines: status?.machines?.map(
        ({
          machine_id,
          state,
          present,
          build_id,
          target_build_id,
          detail,
        }: MachineUpdateState) => ({
          machine_id,
          state,
          present,
          build_id,
          target_build_id,
          detail: recorded_failure_detail(detail),
        }),
      ),
    };
    await Promise.allSettled([
      writeFile(join(o.base, 'evidence/update-failure.json'), JSON.stringify(evidence, null, 2)),
      page?.screenshot({
        path: join(o.base, 'evidence/update-failure.png'),
        animations: 'disabled',
        timeout: 5_000,
      }),
    ]);
    throw error;
  } finally {
    if (readiness_blocked) {
      await rm(ready, { force: true });
      await rename(retained_ready, ready);
    }
    await browser.close();
  }
}
