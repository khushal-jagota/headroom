export function shellRuntimeDomMatches(root:HTMLElement,count:number | null,label:string):boolean {
  const presence=root.querySelector('[data-shell-presence]');
  return root.getAttribute('aria-label')===`${label}.${count === null ? '' : ` ${count} working.`}`
    && (count === null ? presence === null : presence?.textContent?.trim()===`${count} working`
      && Boolean(presence.querySelector('.shell-presence-spin'))===(count>0));
}

export function reviewBadgeDomMatches(root:HTMLElement,count:number):boolean {
  const badge=root.querySelector('.nav-badge');
  return count > 0 ? badge?.textContent===String(count) && !badge.classList.contains('hidden')
    : badge?.classList.contains('hidden')===true && badge.textContent==='';
}

