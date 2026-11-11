/** Runtime authority for the temporary idle publication source. */
import { randomUUID } from 'node:crypto';
import type { CurrentAuthority } from '../../../web/src/lib/navigation/contracts.ts';
import { SignalUnavailable } from '../contracts.ts';
import type { SignalAvailability, SignalInvalidation, SignalReadPermit } from '../contracts.ts';

function identity(): string { return randomUUID().replaceAll('-', ''); }

/** Construct only after the caller establishes readiness and holds runtime ownership.
 * The caller invalidates this authority before shutdown or ownership release.
 * A new installation needs a new instance. Old permits never regain authority.
 */
export class IdlePublicationAuthority {
    private readonly scope = identity();
    private readonly runtime_epoch = identity();
    private readonly installation_id = identity();
    private readonly delivery_session_id = identity();
    private generation = 1;
    private state: 'current' | 'unavailable' = 'current';
    private reason: string | null = null;
    private closed = false;
    private readonly watchers = new Set<() => void>();

    signal_availability(): SignalAvailability {
        return {generation: this.generation, state: this.state, reason: this.reason};
    }

    capture_permit(): SignalReadPermit {
        if (this.state !== 'current') throw new SignalUnavailable(this.reason ?? 'Signal is not active');
        return {scope: this.scope, runtime_epoch: this.runtime_epoch, installation_id: this.installation_id, generation: this.generation};
    }

    validate_permit(permit: SignalReadPermit): void {
        const current = this.capture_permit();
        if (permit.scope !== current.scope || permit.runtime_epoch !== current.runtime_epoch
            || permit.installation_id !== current.installation_id || permit.generation !== current.generation) {
            throw new SignalUnavailable('Signal generation changed');
        }
    }

    capture(): CurrentAuthority {
        const permit = this.capture_permit();
        return {
            delivery_session_id: this.delivery_session_id,
            availability: {state: 'current', generation: String(permit.generation), reason: null},
            permit: {...permit, generation: String(permit.generation)},
        };
    }

    validate(authority: CurrentAuthority): void {
        const current = this.capture();
        if (authority.delivery_session_id !== current.delivery_session_id
            || authority.availability.state !== 'current' || authority.availability.reason !== null
            || authority.availability.generation !== current.availability.generation
            || authority.permit.scope !== current.permit.scope
            || authority.permit.runtime_epoch !== current.permit.runtime_epoch
            || authority.permit.installation_id !== current.permit.installation_id
            || authority.permit.generation !== current.permit.generation) {
            throw new SignalUnavailable('Signal generation changed');
        }
    }

    watch(authority: CurrentAuthority, invalidate: () => void): () => void {
        this.validate(authority);
        // Each subscription owns its registration, even when callers reuse a callback.
        const notify = () => invalidate();
        this.watchers.add(notify);
        return () => { this.watchers.delete(notify); };
    }

    invalidate(reason: SignalInvalidation['reason']): void {
        // Fence every read before notifications. A failed subscriber cannot restore authority.
        this.generation += 1;
        this.state = 'unavailable';
        this.reason = reason;
        const watchers = [...this.watchers];
        this.watchers.clear();
        for (const notify of watchers) {
            try {
                const result: unknown = notify();
                // Callbacks are notifications only. Observe an invalid async subscriber's rejection.
                if (result instanceof Promise) void result.catch(() => {});
            } catch {
                // Notify the remaining subscribers even if one callback fails.
            }
        }
    }

    close(): void {
        if (this.closed) return;
        this.closed = true;
        this.invalidate('detached');
    }
}

/** Public delivery seam. Navigation owns the equivalent consumer interface. */
export type SignalNavigationAuthority = Pick<IdlePublicationAuthority, 'capture' | 'validate' | 'watch'>;
