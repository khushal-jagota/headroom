import { afterEach, describe, expect, it, vi } from 'vitest';
import { askShownByItsCard, messageCopyText, messageTimeLabel } from '../src/lib/conversation/transcript';
import { conversationLensPreference, rememberConversationLensPreference } from '../src/lib/conversation/lens';

describe('message actions', () => {
  it('copies only the words of a message', () => {
    expect(messageCopyText([{ piece: 'text', text: 'One' }, { piece: 'image' }, { piece: 'text', text: 'Two' }])).toBe('One\n\nTwo');
    expect(messageCopyText([{ piece: 'image' }, { piece: 'text', text: '  ' }])).toBe('');
  });

  it('says the time alone today and the day before that', () => {
    const now = new Date(2026, 10, 21, 15, 0);
    const today = messageTimeLabel(new Date(2026, 10, 21, 9, 5).getTime() / 1000, now)!;
    const earlier = messageTimeLabel(new Date(2026, 10, 19, 9, 5).getTime() / 1000, now)!;
    expect(today.short).not.toContain(',');
    expect(earlier.short).toContain(',');
    expect(earlier.short.endsWith(today.short)).toBe(true);
    expect(messageTimeLabel(null)).toBeNull();
    expect(messageTimeLabel(0)).toBeNull();
  });
});

describe('Focus or Full preference', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('opens the way this browser left it, and Focus when it cannot tell', () => {
    const store = new Map<string, string>();
    vi.stubGlobal('window', { localStorage: { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => void store.set(key, value) } });
    expect(conversationLensPreference()).toBe('focus');
    rememberConversationLensPreference('full');
    expect(conversationLensPreference()).toBe('full');
    store.set('panels.conversation.lens', 'sideways');
    expect(conversationLensPreference()).toBe('focus');
    vi.stubGlobal('window', { localStorage: { getItem: () => { throw new Error('blocked'); }, setItem: () => { throw new Error('blocked'); } } });
    expect(conversationLensPreference()).toBe('focus');
    expect(() => rememberConversationLensPreference('full')).not.toThrow();
  });
});

describe('ask rows', () => {
  it('leave a live, unanswered ask to its card and keep every other ask as the record', () => {
    expect(askShownByItsCard({ state: 'live' })).toBe(true);
    expect(askShownByItsCard({ state: 'live', answerFate: 'saved' })).toBe(false);
    for (const state of ['answered', 'dead', 'failed']) expect(askShownByItsCard({ state })).toBe(false);
  });

  it('keep a live ask whose machine availability is unknown, unless its card already says so', () => {
    expect(askShownByItsCard({ state: 'live' }, true, false)).toBe(false);
    expect(askShownByItsCard({ state: 'live' }, true, true)).toBe(true);
    expect(askShownByItsCard({ state: 'answered' }, true, true)).toBe(false);
  });
});
