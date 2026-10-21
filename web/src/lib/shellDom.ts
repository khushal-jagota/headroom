export function reviewBadgeDomMatches(root:HTMLElement,count:number):boolean {
  const badge=root.querySelector('[data-review-badge]');
  return count > 0 ? badge?.textContent===String(count) && !badge.classList.contains('hidden')
    : badge?.classList.contains('hidden')===true && badge.textContent==='';
}

