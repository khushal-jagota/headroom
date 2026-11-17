import { managedFileTargetFromLinkHref } from "./artifactStrip";
import { resolvePreview, targetFromPreviewHref, type ManagedFileTarget } from "./filePreview";
import { isPlainLinkClick, type ClickedLink, type LinkClick } from "./linkClick";

export type RegisterManagedArtifactFrame = (frame: HTMLIFrameElement) => () => void;

/** The parts of a pressed link the decision needs, beyond the ones a plain click needs. */
export type PressedLink = ClickedLink & { isDownload: boolean };

/** The artifact a press on a link asks for, or null when the press is not ours to take.
 *
 * One rule for every surface that shows an artifact where it was pressed. An explicit
 * preview address always asks for the viewer, because that link exists to ask for it. A
 * raw managed link asks for it only when its format has something to show, so a download
 * stays a download. A link the reader marked as a download, and a press asking for
 * another tab, stay with the browser.
 *
 * Nothing here touches the DOM, so every case is testable without a browser.
 */
export function artifactForPressedLink(
  link: PressedLink,
  press: LinkClick
): ManagedFileTarget | null {
  if (link.isDownload || !isPlainLinkClick(link, press)) return null;
  const explicit = targetFromPreviewHref(link.href);
  if (explicit) return explicit;
  const managed = managedFileTargetFromLinkHref(link.href);
  if (!managed || resolvePreview(managed).kind === "download") return null;
  return managed;
}

/** The same decision, read off a press that landed on a page.
 *
 * The one place the rule meets the DOM: it finds the link under the press and hands the
 * three facts about it to the rule above.
 */
export function artifactForPressOnThePage(event: MouseEvent): ManagedFileTarget | null {
  if (event.defaultPrevented) return null;
  const anchor = (event.target as Element | null)?.closest?.("a[href]");
  if (!anchor) return null;
  return artifactForPressedLink(
    {
      href: anchor.getAttribute("href"),
      target: anchor.getAttribute("target"),
      isDownload: anchor.hasAttribute("download")
    },
    event
  );
}

/** What held the pressed link: the nearest `data-artifact-origin` around it, or null.
 *
 * The artifact opens over the page either way. The holder only decides which controls
 * stand under it: a proposal's decisions, a message's reply, or the page's own bar.
 */
export function artifactOriginOfPress(event: MouseEvent): string | null {
  const target = event.target as Element | null;
  return target?.closest?.("[data-artifact-origin]")?.getAttribute("data-artifact-origin") ?? null;
}
