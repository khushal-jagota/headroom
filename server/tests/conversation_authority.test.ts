import { expect, it } from 'vitest';
import { IdlePublicationAuthority } from '../conversation/publication/authority.ts';
import { SignalUnavailable } from '../conversation/contracts.ts';

// HTTP cannot invalidate ownership between capture and delivery, or register an in-process failing subscriber.
it('fences permits before notification and isolates failing or removed subscriptions', () => {
    const source = new IdlePublicationAuthority();
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

// The request list cannot forge private authority identities or exercise idempotent attachment closure.
it('keeps source and wire permits distinct and rejects stale installations', () => {
    const source = new IdlePublicationAuthority();
    const authority = source.capture();
    expect(authority.permit.generation).toBe('1');
    expect(authority.availability.generation).toBe('1');
    expect(source.capture_permit().generation).toBe(1);
    source.validate(authority);
    source.validate_permit(source.capture_permit());
    const other = new IdlePublicationAuthority();
    expect(() => other.validate(authority)).toThrow(SignalUnavailable);
    expect(() => other.validate_permit(source.capture_permit())).toThrow(SignalUnavailable);
    expect(() => source.validate({...authority, permit: {...authority.permit, generation: '01'}})).toThrow(SignalUnavailable);
    source.close();
    const availability = source.signal_availability();
    source.close();
    expect(source.signal_availability()).toEqual(availability);
    expect(availability).toEqual({state: 'unavailable', generation: 2, reason: 'detached'});
    expect(() => source.validate(authority)).toThrow(SignalUnavailable);
});
