import { collapseConversationRow, fadeConversationExit } from './surfaceMotion';

/** Keep only disappearing local bubbles until their exit frees the occupied space. */
export class MessageExits<T extends { key: string; local: unknown; item: unknown }> {
  private nodes = new Map<string, HTMLElement>();
  private leaving = new Map<string, { slot: T; completed: boolean; cancelled: boolean }>();
  register = (key: string, node: HTMLElement): (() => void) => {
    this.nodes.set(key, node);
    return () => { if (this.nodes.get(key) === node) this.nodes.delete(key); };
  };
  retain(next: T[], previous: readonly T[], changed: () => void): T[] {
    const present = new Set(next.map(slot => slot.key));
    for (const key of present) {
      const exit = this.leaving.get(key);
      if (exit) { exit.cancelled = true; this.leaving.delete(key); }
    }
    const result = [...next];
    for (const slot of previous) {
      if (slot.local === null || slot.item !== null || present.has(slot.key)) continue;
      let exit = this.leaving.get(slot.key);
      if (!exit) {
        const node = this.nodes.get(slot.key);
        if (!node || node.querySelector('[data-conversation-exited]')) continue;
        exit = {slot, completed:false, cancelled:false};
        this.leaving.set(slot.key, exit);
        const operation = exit;
        void (async () => {
          await fadeConversationExit(node);
          const restore = !operation.cancelled ? await collapseConversationRow(node) : undefined;
          if (operation.cancelled) {
            restore?.(); node.style.removeProperty('opacity');
          } else { operation.completed = true; changed(); }
        })();
      }
      if (!exit.completed) {
        const previousIndex = previous.indexOf(slot);
        const following = previous.slice(previousIndex + 1).find(candidate=>present.has(candidate.key));
        const at = following ? result.findIndex(candidate=>candidate.key===following.key) : result.length;
        result.splice(at,0,slot);
      } else this.leaving.delete(slot.key);
    }
    return result;
  }
}
