/** Whether a click on a link is the app's to take over.
 *
 * Two screens catch clicks on links inside them and do something other than navigate:
 * the Atlas panel moves the world, and the Ticket screen opens an artifact in place.
 * Both answer the same question first, and both must answer it the same way — a click
 * asking for a new tab or a new window stays the reader's, so the ordinary address still
 * opens there.
 *
 * Nothing here touches the DOM, so it is testable without a browser.
 */

/** The parts of the clicked anchor the decision needs. */
export type ClickedLink = {
  // The `href` attribute as written, not the resolved property: the app writes hash
  // text, and the property comes back as a whole URL.
  href: string | null;
  target?: string | null;
};

/** The parts of the click the decision needs. */
export type LinkClick = {
  button?: number;
  metaKey?: boolean;
  ctrlKey?: boolean;
  shiftKey?: boolean;
  altKey?: boolean;
};

export function isPlainLinkClick(link: ClickedLink, click: LinkClick): boolean {
  if (click.button !== undefined && click.button !== 0) return false;
  if (click.metaKey || click.ctrlKey || click.shiftKey || click.altKey) return false;
  if (link.target) return false;
  return true;
}
