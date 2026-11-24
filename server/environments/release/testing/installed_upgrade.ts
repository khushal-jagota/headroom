/** Execute the normal owner action against the previously installed home and its existing machine. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import { recorded_failure_detail } from '../../../conversation/logic/backend_events.ts';
import { chromium } from 'playwright';
import { healthy } from '../../updates/installer/health.ts';
import { HOME_HEALTH_TIMEOUT_SECONDS } from '../../../installation/configuration.ts';
import { HOME_MACHINE_ID } from '../../../execution_identity.ts';
import type { MachineUpdateState } from '../../updates/view_contracts.ts';
import { smoke_file_turn, verify_smoke_history } from '../../../tools/smoke/steps.ts';
import { DEFAULT_BACKEND_PEER_REPLY } from '../../../tools/smoke/codex_peer.ts';
import { credential_path } from '../../../executor/credential_file.ts';

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
  const turn = (after_rollback = false) =>
    smoke_file_turn(
      url,
      {
        model: 'TEST',
        reply: DEFAULT_BACKEND_PEER_REPLY,
        workspace,
        after_rollback,
      },
      o.machine_id,
    );
  const before = await turn();
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
  const browser = await chromium.launch();
  let phase = 'installing and rolling back';
  try {
    const page = await browser.newPage();
    await page.goto(url);
    // Occupy this disposable home's port immediately after the old listener stops.
    // The verified candidate bytes are intact; its actual native service fails to bind.
    const fault = createServer((_request, response) => {
      response.writeHead(503, { Connection: 'close' });
      response.end();
    });
    const readJournal = async () =>
      JSON.parse(await readFile(join(o.base, 'installed/data/update-attempt.json'), 'utf8'));
    let failedAttemptId: string;
    try {
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
      await wait_for(
        () =>
          new Promise<boolean>((done, reject) => {
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
          }),
        'injecting failure on candidate bind',
        20,
      );
      await wait_for(
        async () => {
          const attempt = await readJournal();
          if (attempt.to_sha !== candidate || !attempt.steps?.rollback_service_stopped)
            return false;
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
              preserved_attachment: before.stored_file_id,
            },
            null,
            2,
          ),
        );
        assert.ok(
          !attempt.steps?.rollback_failed && attempt.steps?.rollback_healthy,
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
    await o.verify_rollback?.();
    assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
    const preserved = { ...before, model: 'TEST' };
    await verify_smoke_history(url, preserved);
    const recovered = await turn(true);
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
    phase = 'retrying the update';
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
    await wait_for(async () => {
      const status = await read_status();
      if (status?.attempt?.attempt_id === rolledBackAttempt) return false;
      if (status?.attempt?.state === 'failed') throw new Error(status.attempt.detail);
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
    const after = await turn();
    assert.notEqual(before.conversation_id, after.conversation_id);
    await verify_smoke_history(url, preserved);
    phase = 'reopening the update screen';
    await page.reload();
    await headline.getByText('Up to date', { exact: true }).waitFor();
    for (const id of enrolled)
      await card.locator(`[data-machine-update="${id}"][data-state="current"]`).waitFor();
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
    await writeFile(
      join(o.base, 'evidence/upgrade.json'),
      JSON.stringify(
        {
          predecessor: o.predecessor,
          candidate,
          machine_id: o.machine_id,
          reported_build: expectedBuild,
          conversations: [before.conversation_id, after.conversation_id],
          rollback_sha: o.predecessor,
          recovered_conversation: recovered.conversation_id,
          original_linux_start: originalStart,
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
    const evidence = {
      phase,
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
    await browser.close();
  }
}
