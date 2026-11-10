/** Hover previews supplement a row. Tab continues through the sidebar's rows;
 * pointer users can still press and edit every control in the preview. */
export function skipPreviewTabStops(panel: HTMLElement) {
  const controls = 'a[href],area[href],button,input,select,textarea,summary,iframe,object,embed,audio[controls],video[controls],[contenteditable]:not([contenteditable="false"]),[tabindex]';
  const exclude = () => {
    for (const control of panel.querySelectorAll<HTMLElement>(controls)) {
      if (control.getAttribute('tabindex') !== '-1') control.setAttribute('tabindex', '-1');
    }
  };
  exclude();
  // Detail reads and saves replace controls or restore their component-owned tabindex.
  const observer = new MutationObserver(exclude);
  observer.observe(panel, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['tabindex', 'href', 'contenteditable', 'controls']
  });
  return { destroy: () => observer.disconnect() };
}
