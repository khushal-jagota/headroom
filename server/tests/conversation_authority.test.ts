import { expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createDatabase } from '../core/database/index.ts';
import { createIdlePublicationAuthority, IdlePublicationAuthority } from '../conversation/publication/authority.ts';
import { SignalUnavailable } from '../conversation/contracts.ts';

// HTTP cannot invalidate ownership between capture and delivery, or register an in-process failing subscriber.
it('fences permits before notification and isolates failing or removed subscriptions', () => {
    const source = new IdlePublicationAuthority({scope: 'scope', runtime_epoch: 'epoch', installation_id: 'installation', delivery_session_id: 'delivery'});
    const authority = source.capture();
    const permit = source.capture_permit();
    let removedCalls = 0;
    const removed = () => { removedCalls += 1; };
    const unsubscribe = source.watch(authority, removed);
    unsubscribe();
    unsubscribe();
    let notified = 0;
    const observed: unknown[] = [];
    source.watch(authority, () => {
        observed.push(source.signal_availability());
        try { source.validate(authority); observed.push('accepted stale authority'); }
        catch (error) { observed.push(error instanceof SignalUnavailable); }
        try { source.validate_permit(permit); observed.push('accepted stale permit'); }
        catch (error) { observed.push(error instanceof SignalUnavailable); }
        throw new Error('subscriber failed');
    });
    source.watch(authority, () => { notified += 1; });
    source.invalidate('authority_lost');
    expect(notified).toBe(1);
    expect(removedCalls).toBe(0);
    expect(observed).toEqual([{state: 'unavailable', generation: 2, reason: 'authority_lost'}, true, true]);
    expect(() => source.capture()).toThrow(SignalUnavailable);
    expect(() => source.watch(authority, () => {})).toThrow(SignalUnavailable);
    source.invalidate('shutdown');
    expect(notified).toBe(1);
});

// The list cannot restart its hosts. A real reopen proves that scope belongs to the database,
// while runtime, installation, and delivery identities change without a stale permit revival.
it('keeps source and wire permits distinct and rejects stale installations', () => {
    const root = mkdtempSync(join(tmpdir(), 'panels-idle-authority-'));
    const path = join(root, 'database.sqlite3');
    let database: ReturnType<typeof createDatabase> | undefined;
    try {
        // Reuse the write-door tests' narrow Python SQLite setup, with only the source meta schema.
        execFileSync('python3', ['-c', `
import sqlite3, sys
with sqlite3.connect(sys.argv[1]) as conn:
    conn.execute('CREATE TABLE signal_publication_meta (singleton INTEGER PRIMARY KEY CHECK(singleton=1), scope TEXT NOT NULL, runtime_epoch TEXT NOT NULL, sequence INTEGER NOT NULL)')
`, path]);
        database = createDatabase(path);
        const source = createIdlePublicationAuthority(database);
        const authority = source.capture();
        const permit = source.capture_permit();
        expect(authority.permit.generation).toBe('1');
        expect(authority.availability.generation).toBe('1');
        expect(permit.generation).toBe(1);
        source.validate(authority);
        source.validate_permit(permit);
        expect(() => source.validate({...authority, permit: {...authority.permit, generation: '01'}})).toThrow(SignalUnavailable);
        source.close();
        const availability = source.signal_availability();
        source.close();
        expect(source.signal_availability()).toEqual(availability);
        expect(availability).toEqual({state: 'unavailable', generation: 2, reason: 'detached'});
        expect(() => source.validate(authority)).toThrow(SignalUnavailable);
        database.close();
        database = undefined;

        database = createDatabase(path);
        const restarted = createIdlePublicationAuthority(database);
        const next = restarted.capture();
        expect(next.permit.scope).toBe(authority.permit.scope);
        expect(next.permit.runtime_epoch).not.toBe(authority.permit.runtime_epoch);
        expect(next.permit.installation_id).not.toBe(authority.permit.installation_id);
        expect(next.delivery_session_id).not.toBe(authority.delivery_session_id);
        expect(() => restarted.validate(authority)).toThrow(SignalUnavailable);
        expect(() => restarted.validate_permit(permit)).toThrow(SignalUnavailable);
        expect(database.read(tx => tx.get('SELECT scope,runtime_epoch,sequence FROM signal_publication_meta WHERE singleton=1')))
            .toEqual({scope: next.permit.scope, runtime_epoch: next.permit.runtime_epoch, sequence: 0});
        restarted.close();
    } finally {
        database?.close();
        rmSync(root, {recursive: true, force: true});
    }
});
