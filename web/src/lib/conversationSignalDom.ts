import { conversationSignalKind, type ConversationSignal } from './conversationSignal';
export function actualMark(root:Element) {
  const element=root.querySelector('[data-conversation-signal]');
  return element ? [element.getAttribute('data-conversation-signal'),element.closest('[data-conversation-id]')?.getAttribute('data-conversation-id') ?? null] : [null,null];
}
export function expectedMark(signal:Partial<ConversationSignal> | undefined,id:string | null | undefined, root?:Element | null) {
  const value=conversationSignalKind(root?.querySelector('[data-machine-activity-unknown="true"]') ? {...signal,working:false} : signal);return [value,value ? id ?? null : null];
}
