/** The recipient runtime retains the complete delivery, including its final local writes. */
import { Deferred } from '../core/deferred.ts';
const deliveries = new WeakMap<object, Set<Promise<void>>>();
export function own_recipient_delivery<T>(owner: object, operation: () => Promise<T>): Promise<T> {
  let pending = deliveries.get(owner);
  if (!pending) {
    pending = new Set();
    deliveries.set(owner, pending);
  }
  const settlement = new Deferred<void>();
  pending.add(settlement.promise);
  const finish = () => {
    pending.delete(settlement.promise);
    settlement.resolve();
  };
  try {
    return operation().finally(finish);
  } catch (error) {
    finish();
    return Promise.reject(error);
  }
}
export function recipient_delivery_settlements(owner: object): readonly Promise<void>[] {
  return [...(deliveries.get(owner) ?? [])];
}
