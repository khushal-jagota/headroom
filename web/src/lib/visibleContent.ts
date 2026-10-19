/** Native preview resources exist only near their reader's viewport. */
export function observeVisibleContent(
  element: HTMLElement,
  update: (visible: boolean, measuredHeight: number) => void
): () => void {
  const root = element.closest('[data-conversation-thread]');
  let intersecting = false;
  let height = 0;
  const publish = () => update(intersecting && document.visibilityState !== 'hidden', height);
  const resize = new ResizeObserver(entries => {
    const next = entries[0]?.borderBoxSize[0]?.blockSize ?? element.getBoundingClientRect().height;
    if (next > 0) height = next;
  });
  const intersection = new IntersectionObserver(entries => {
    intersecting = entries[0]?.isIntersecting ?? false;
    publish();
  }, { root, rootMargin: '256px' });
  resize.observe(element);
  intersection.observe(element);
  document.addEventListener('visibilitychange', publish);
  return () => {
    resize.disconnect();
    intersection.disconnect();
    document.removeEventListener('visibilitychange', publish);
  };
}
