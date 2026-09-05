/** Whether a click on a link is the app's to take over.
 *
 * The Ticket screen catches clicks on links inside it to open an artifact in place.
 * It first answers whether the click is the app's to handle: a click asking for a new
 * tab or window stays the reader's, so the ordinary address still opens there.
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
