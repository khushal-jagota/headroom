import type { NavigationPublication } from './collection';

export type DurableFact = 'message_for_owner' | 'last_turn_failed';
export type FactProvenance = {
  readonly conversationId: string;
  readonly publicationId: number;
  readonly admissionToken: string;
  readonly boundary: number;
  readonly continuity: NavigationPublication['continuity'];
  readonly mode: NavigationPublication['mode'];
  readonly authority: NavigationPublication['authority'];
};
export type KnownFact<T> = { readonly state: 'fresh' | 'retained'; readonly value: T; readonly provenance: FactProvenance }
  | { readonly state: 'unknown' };
export type ConversationKnowledge = {
  readonly working: KnownFact<boolean>;
  readonly awaiting_answer: KnownFact<boolean>;
  readonly message_for_owner: KnownFact<boolean>;
  readonly last_turn_failed: KnownFact<boolean>;
};
export type EffectiveNavigationView = {
  readonly publication: NavigationPublication;
  readonly runtimeCurrent: boolean;
  readonly conversations: ReadonlyMap<string, ConversationKnowledge>;
};
const unknown: KnownFact<never> = Object.freeze({ state: 'unknown' });

/** Shared knowledge bookkeeping: never modifies canonical records or invents a value. */
export function createNavigationKnowledge() {
  let view: EffectiveNavigationView | undefined;
  const invalidated = new Map<string, Partial<Record<DurableFact, number>>>();
  function invalidateRuntime(): EffectiveNavigationView | undefined {
    if (!view) return;
    const conversations = new Map<string, ConversationKnowledge>();
    for (const [id, knowledge] of view.conversations) {
      const retain = (fact: KnownFact<boolean>): KnownFact<boolean> => fact.state === 'unknown'
        ? fact : Object.freeze({ ...fact, state: 'retained' });
      conversations.set(id, Object.freeze({ working: unknown, awaiting_answer: unknown,
        message_for_owner: retain(knowledge.message_for_owner), last_turn_failed: retain(knowledge.last_turn_failed) }));
    }
    view = Object.freeze({ ...view, runtimeCurrent: false, conversations });
    return view;
  }
  return {
    getView: () => view,
    loseRuntime: invalidateRuntime,
    accept(publication: NavigationPublication): EffectiveNavigationView {
      const conversations = new Map<string, ConversationKnowledge>();
      for (const record of publication.records) {
        if (record.kind !== 'conversation') continue;
        const { attention } = record.value;
        const provenance = (boundary: number): FactProvenance => Object.freeze({
          conversationId: record.id, publicationId: publication.publicationId,
          admissionToken: publication.admissionToken, boundary, continuity:publication.continuity, mode:publication.mode, authority: publication.authority });
        const known = (value: boolean, boundary: number): KnownFact<boolean> => Object.freeze({state:'fresh',value,provenance:provenance(boundary)});
        const durable = (fact: DurableFact, boundary: number): KnownFact<boolean> => {
          const required = invalidated.get(record.id)?.[fact];
          if (required !== undefined && boundary < required) return unknown;
          if (required !== undefined) delete invalidated.get(record.id)![fact];
          return known(attention.durable[fact], boundary);
        };
        conversations.set(record.id, Object.freeze({
          working: attention.current === null ? unknown : known(attention.current.signal.working, record.value.latest_sequence),
          awaiting_answer: attention.current === null ? unknown : known(attention.current.awaiting_answer, record.value.latest_sequence),
          message_for_owner: durable('message_for_owner', record.value.owner_read_through_sequence),
          last_turn_failed: durable('last_turn_failed', record.value.error_acknowledged_through_sequence),
        }));
      }
      for (const id of invalidated.keys()) if (!conversations.has(id)) invalidated.delete(id);
      view = Object.freeze({publication,runtimeCurrent:publication.mode === 'current',conversations});
      return view;
    },
    invalidateDurableFact(conversationId: string, fact: DurableFact, confirmedThroughSequence: number): EffectiveNavigationView | undefined {
      if (!Number.isSafeInteger(confirmedThroughSequence) || confirmedThroughSequence < 0) throw new Error('Invalid confirmed read boundary');
      const requirements = invalidated.get(conversationId) ?? {};
      requirements[fact] = Math.max(requirements[fact] ?? 0, confirmedThroughSequence);
      const current = view?.conversations.get(conversationId);
      const prior = current?.[fact];
      // A newer canonical observation can already cover a late, older confirmation.
      if (prior?.state === 'fresh' && prior.provenance.boundary >= requirements[fact]!) {
        delete requirements[fact];
        if (Object.keys(requirements).length) invalidated.set(conversationId,requirements);
        else invalidated.delete(conversationId);
        return view;
      }
      invalidated.set(conversationId, requirements);
      if (!view) return;
      if (!current) return view;
      const conversations = new Map(view.conversations);
      conversations.set(conversationId, Object.freeze({...current,[fact]:unknown}));
      view = Object.freeze({...view,conversations});
      return view;
    },
    clear() { view = undefined; invalidated.clear(); },
  };
}
