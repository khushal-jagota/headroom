/** A link inside the Atlas panel, turned into a place in the world.
 *
 * Atlas raises real screens of the app over the world, and those screens link the way
 * they link everywhere else — at `#/workspace/…`. Inside Atlas a link like that must
 * move the world, not leave it. This is the whole of the decision: an href and a click
 * in, a selection or nothing out. An href that maps to no place in Atlas returns null
 * and the link is left alone.
 *
 * Nothing here touches the DOM, so it is testable without a browser.
 */
import { isPlainLinkClick, type ClickedLink, type LinkClick } from "../linkClick";
import { parseWorkspaceAddress } from "../workspaceAddress";
import type { AtlasSelection } from "./contracts";

/** The parts of the clicked anchor the decision needs. */
export type AtlasLink = ClickedLink;

/** The parts of the click the decision needs. */
export type AtlasLinkClick = LinkClick;

export function selectionForHref(href: string | null | undefined): AtlasSelection | null {
  if (!href) return null;
  // The server builds both `#/workspace/…` and `/#/workspace/…`.
  const hash = href.startsWith("/#") ? href.slice(1) : href;
  if (!hash.startsWith("#")) return null;
  const address = parseWorkspaceAddress(hash);
  if (address) {
    const selection = address.selection;
    if (selection.kind === "ticket") return { kind: "ticket", id: selection.id };
    if (selection.kind === "item") return { kind: "item", id: selection.id };
    // The whole Workspace and the Chief of Staff are no place in the world.
    return null;
  }
  // The supervisor panel's own link to its Item. `parseWorkspaceAddress` does not
  // know this form, and no screen inside the panel would catch it.
  const text = hash.slice(1);
  const queryIndex = text.indexOf("?");
  if (queryIndex < 0) return null;
  if (text.slice(0, queryIndex).replace(/\/+$/, "") !== "/sprint") return null;
  const item = new URLSearchParams(text.slice(queryIndex + 1)).get("item");
  return item ? { kind: "item", id: item } : null;
}

export function selectionForLinkClick(
  link: AtlasLink,
  click: AtlasLinkClick
): AtlasSelection | null {
  // A click meant for a new tab or a new window stays the reader's, so the Workspace
  // still opens beside Atlas.
  if (!isPlainLinkClick(link, click)) return null;
  return selectionForHref(link.href);
}
