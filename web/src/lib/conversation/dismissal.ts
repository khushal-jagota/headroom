/** Dismiss on the press origin so a selection may finish outside the card. */
export function isPrimaryPressOutsideConversation(event: PointerEvent): boolean {
  return event.isPrimary && event.button === 0 && event.target instanceof Element &&
    event.target.closest("[data-conversation-pane]") === null;
}
