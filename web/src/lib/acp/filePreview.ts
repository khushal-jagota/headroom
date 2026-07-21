import { targetFromHref } from '../filePreview';

export function acpFilePreviewTarget(href: string, label = '') {
  return targetFromHref(href, label);
}
