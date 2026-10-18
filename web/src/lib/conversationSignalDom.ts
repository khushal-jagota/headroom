import { conversationSignalKind, type ConversationSignal } from './conversationSignal';
export function actualMark(root:Element) {
  const element=root.querySelector('[data-conversation-signal]');
  return element ? [element.getAttribute('data-conversation-signal'),element.closest('[data-conversation-id]')?.getAttribute('data-conversation-id') ?? null] : [null,null];
}
export function expectedMark(signal:Partial<ConversationSignal> | undefined,id:string | null | undefined) {
  const value=conversationSignalKind(signal);return [value,value ? id ?? null : null];
}
