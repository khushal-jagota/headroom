/** Execute the normal owner action against the previously installed home and its existing machine. */
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { execFile } from 'node:child_process';
import { readFile, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';
import { promisify } from 'node:util';
import { chromium } from 'playwright';
import { smoke_file_turn } from '../../../tools/smoke/steps.ts';
import { DEFAULT_BACKEND_PEER_REPLY } from '../../../tools/smoke/codex_peer.ts';
import { credential_path } from '../../../executor/credential_file.ts';

interface Options {
  base: string;
  port: number;
  service: string;
  machine_id: string;
  joined_state: string;
  predecessor: string;
}

async function wait_for(read: () => Promise<boolean>, description: string, seconds = 600) {
  const until = performance.now() + seconds * 1000;
  while (performance.now() < until) {
    if (await read()) return;
    await delay(500);
  }
  throw new Error('Timed out ' + description);
}

export async function qualify_installed_upgrade(o: Options): Promise<void> {
  const url = `http://127.0.0.1:${o.port}`;
  const candidate = process.env.GITHUB_SHA!;
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
  const turn = () =>
    smoke_file_turn(
      url,
      {
        model: 'TEST',
        reply: DEFAULT_BACKEND_PEER_REPLY,
        workspace: o.base,
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
  try {
    const page = await browser.newPage();
    await page.goto(url);
    // Occupy this disposable home's port immediately after the old listener stops.
    // The verified candidate bytes are intact; its actual native service fails to bind.
    const fault = createServer((_request, response) => {
      response.writeHead(503, { Connection: 'close' });
      response.end();
    });
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
      if (await confirm.isVisible()) await confirm.click();
      const accepted = await started;
      assert.equal(accepted.ok(), true, await accepted.text());
      assert.equal((await accepted.json()).attempt.to_sha, candidate);
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
      );
      const journal = join(o.base, 'installed/data/update-attempt.json');
      await wait_for(async () => {
        const attempt = JSON.parse(await readFile(journal, 'utf8'));
        return attempt.to_sha === candidate && Boolean(attempt.steps?.rollback_service_stopped);
      }, 'waiting for the installer to stop the failed candidate');
    } finally {
      if (fault.listening) await new Promise<void>((done) => fault.close(() => done()));
    }
    await wait_for(async () => {
      const status = await read_status();
      return status?.attempt?.state === 'failed' && status.app_sha === o.predecessor;
    }, 'waiting for previous version after rollback');
    assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
    await turn();
    const rolledBackAttempt = (await read_status()).attempt.attempt_id;
    await page.goto(url);
    await page
      .getByRole('button', { name: /retry|try again/i })
      .first()
      .click();
    await wait_for(async () => {
      const status = await read_status();
      if (status?.attempt?.attempt_id === rolledBackAttempt) return false;
      if (status?.attempt?.state === 'failed') throw new Error(status.attempt.detail);
      return (
        status?.app_sha === candidate &&
        status?.attempt?.state === 'done' &&
        status.machines.some(
          (m: { machine_id: string; state: string }) =>
            m.machine_id === o.machine_id && m.state === 'current',
        )
      );
    }, 'waiting for installed home and joined machine to run the candidate');
    assert.deepEqual(await readFile(credential_path(o.joined_state)), credential);
    const after = await turn();
    assert.notEqual(before.conversation_id, after.conversation_id);
    const previousConversation = await fetch(
      url + '/api/conversation/conversations/' + before.conversation_id + '/events',
    );
    assert.equal(previousConversation.status, 200);
    await page.reload();
    await page.getByText('Up to date', { exact: true }).first().waitFor();
    await page.screenshot({ path: join(o.base, 'evidence/update-complete.png') });
    await writeFile(
      join(o.base, 'evidence/upgrade.json'),
      JSON.stringify(
        {
          predecessor: o.predecessor,
          candidate,
          machine_id: o.machine_id,
          conversations: [before.conversation_id, after.conversation_id],
          rollback_sha: o.predecessor,
          publication: JSON.parse(await readFile(join(o.base, 'feed/publication.json'), 'utf8')),
        },
        null,
        2,
      ),
    );
  } finally {
    await browser.close();
  }
}
