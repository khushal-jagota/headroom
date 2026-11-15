import { managedFileTargetFromLinkHref } from "./artifactStrip";
import { resolvePreview, targetFromPreviewHref, type ManagedFileTarget } from "./filePreview";
import { isPlainLinkClick, type ClickedLink, type LinkClick } from "./linkClick";

/** The sandbox can only announce a primary press, never request a state or action. */
export const MANAGED_ARTIFACT_PRIMARY_PRESS = "panels:managed-artifact-primary-press";
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
