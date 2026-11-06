/** Rail decoration only. Selection and interaction remain owned by the existing controls. */
type Box = { left: number; top: number; width: number; height: number };
type Layer = { node: HTMLDivElement; box: string; animation: Animation | null; fade: Animation | null; arrival: number };
type Host = { node: HTMLElement; group: Layer; item: Layer; hover: Layer; focus: Layer };
type Target = { node: HTMLElement; host: Host; group: HTMLElement | null; itemBox: Box; groupBox: Box | null };
const properties = ['left', 'top', 'width', 'height'] as const;
const targets = '.item-head, .worker-row, .goal, .settled-title, .utility';

export function workspaceRailWash(root: HTMLElement) {
  const hosts = new Map<HTMLElement, Host>();
  const ghosts = new Map<HTMLElement, { target: HTMLElement; group: boolean; animation: Animation }>();
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  let current: Target | null = null;
  let hovered: HTMLElement | null = null;
  let hoverHeld: { node: HTMLElement; until: number } | null = null;
  let frame = 0;
  let followUntil = 0;
  let disposed = false;
  let lastHover: HTMLElement | null = null;
  const observed = new Set<Element>();
  const css = (name: string) => getComputedStyle(root).getPropertyValue(name).trim();
  const ms = (name: string) => {
    const value = css(name);
    return parseFloat(value) * (value.endsWith('ms') ? 1 : 1000);
  };
  function layer(host: HTMLElement, kind: string): Layer {
    const node = document.createElement('div');
    node.className = `rail-wash rail-wash--${kind}`;
    node.hidden = true;
    node.setAttribute('aria-hidden', 'true');
    if (kind === 'focus') host.append(node); else host.prepend(node);
    return { node, box: '', animation: null, fade: null, arrival: 0 };
  }
  function discover() {
    for (const node of root.querySelectorAll<HTMLElement>('.active-work-scroll, .settled-body, .rail-utilities')) {
      if (!hosts.has(node)) { hosts.set(node, {node, group: layer(node,'group'), item:layer(node,'item'), hover:layer(node,'hover'), focus:layer(node,'focus')}); resize.observe(node); }
    }
    for (const node of root.querySelectorAll('.manager-card, .identity, [data-goal-status], .worker-row, .goal, .settled-row')) { if (!observed.has(node)) {observed.add(node);resize.observe(node);} }
    for (const node of observed) if (!root.contains(node)) {resize.unobserve(node);observed.delete(node);}
    for (const [node,host] of hosts) if (!root.contains(node)) {
      resize.unobserve(node);
      for (const part of [host.group,host.item,host.hover,host.focus]) {part.animation?.cancel();part.fade?.cancel();part.node.remove();}
      hosts.delete(node);
    }
  }
  function geometry(node: HTMLElement): Target | null {
    const hostNode = node.closest<HTMLElement>('.active-work-scroll, .settled-body, .rail-utilities');
    const host = hostNode && hosts.get(hostNode);
    if (!host || !root.contains(node) || !node.getClientRects().length) return null;
    const base = host.node.getBoundingClientRect();
    const local = (rect: DOMRect): Box => ({left:rect.left-base.left+host.node.scrollLeft,top:rect.top-base.top+host.node.scrollTop,width:rect.width,height:rect.height});
    const group = node.closest<HTMLElement>('.manager-card');
    const groupBox = group ? local(group.getBoundingClientRect()) : null;
    const itemBox = local((node.closest('.settled-row') ?? node).getBoundingClientRect());
    const inset = parseFloat(css('--space-1'));
    if (groupBox) {
      itemBox.left = groupBox.left+inset;
      itemBox.width = groupBox.width-2*inset;
      if (node.matches('.item-head')) {
        // A Manager's name is selected, hovered and focused like any other row: one fill
        // around the name's line, inside the same context tint its goals and Tickets get.
        const head = local(node.getBoundingClientRect());
        itemBox.top = head.top-inset;
        itemBox.height = head.height+2*inset;
      }
    }
    return {node,host,group,itemBox,groupBox};
  }
  const style = (box: Box) => Object.fromEntries(properties.map(key=>[key,`${box[key]}px`]));
  function draw(part: Layer, box: Box, travel = false) {
    const next = style(box);
    const key = JSON.stringify(next);
    if (travel) part.arrival = performance.now()+ms('--motion-wash');
    if (key === part.box) return;
    const from = Object.fromEntries(properties.map(key=>[key,getComputedStyle(part.node)[key]]));
    const remaining = part.arrival-performance.now();
    part.animation?.cancel();part.animation=null;
    Object.assign(part.node.style,next);
    if (!part.node.hidden && !media.matches && remaining > 0) {
      part.animation = part.node.animate([from,next],{duration:remaining,easing:css('--motion-move')});
    }
    part.box=key;
  }
  function hide(part: Layer) {
    part.animation?.cancel();part.fade?.cancel();part.animation=null;part.fade=null;part.arrival=0;part.node.hidden=true;
  }
  function fadeIn(part: Layer, box: Box, duration: number) {
    hide(part);draw(part,box);part.node.hidden=false;
    if (!media.matches) part.fade=part.node.animate([{opacity:0},{opacity:1}],{duration,easing:'ease'});
  }
  function fadeOut(part: Layer, target: HTMLElement, group: boolean, duration: number) {
    if (part.node.hidden) return;
    if (!media.matches) {
      const ghost = part.node.cloneNode(true) as HTMLElement;
      for (const key of properties) ghost.style[key]=getComputedStyle(part.node)[key];
      ghost.classList.add('rail-wash--leaving');
      part.node.after(ghost);
      const animation=ghost.animate([{opacity:getComputedStyle(part.node).opacity},{opacity:0}],{duration,easing:'ease'});
      ghosts.set(ghost,{target,group,animation});
      animation.onfinish=()=>{ghost.remove();ghosts.delete(ghost);};
    }
    hide(part);
  }
  const identity = (node: HTMLElement) => node.dataset.railMotion ?? node.dataset.goalId ?? node.getAttribute('href') ?? node.closest<HTMLElement>('[data-settled-item]')?.dataset.settledItem ?? (node.matches('.item-head') ? node.closest<HTMLElement>('[data-rail-motion]')?.dataset.railMotion : undefined);
  function select(next: Target | null) {
    // A bucket move recreates a keyed row in a different bucket. It is still the same selection.
    if (current && next && current.host===next.host && identity(current.node) && identity(current.node)===identity(next.node)) {
      if (current.node!==next.node) {current.host.item.arrival=0;current.host.item.animation?.cancel();current.host.item.animation=null;}
      current=next;return;
    }
    if (current?.node === next?.node && current?.host === next?.host && current?.group === next?.group) {current=next;return;}
    const prev=current;
    if (next && hovered === next.node) {
      hoverHeld={node:next.node,until:performance.now()+ms('--motion-slow')};hovered=null;
    }
    const sameHost=prev && next && prev.host===next.host;
    const sameGroup=sameHost && prev.group && prev.group===next.group;
    const deepen=sameHost && prev.group && next.group && prev.group!==next.group;
    if (prev && !sameGroup) {
      fadeOut(prev.host.item,prev.node,false,ms(deepen?'--motion-wash':'--motion-base'));
      if (!deepen) fadeOut(prev.host.group,prev.node,true,ms('--motion-base'));
    }
    if (next) {
      if (sameGroup) draw(next.host.item,next.itemBox,true);
      else fadeIn(next.host.item,next.itemBox,ms(deepen?'--motion-wash':'--motion-slow'));
      if (next.groupBox) {
        if (next.host.group.node.hidden) fadeIn(next.host.group,next.groupBox,ms(sameGroup?'--motion-base':'--motion-slow'));
        else if (sameGroup || deepen) draw(next.host.group,next.groupBox,Boolean(deepen));
        else fadeIn(next.host.group,next.groupBox,ms('--motion-slow'));
        next.host.group.node.hidden=false;
      } else if (!next.host.group.node.hidden) fadeOut(next.host.group,next.node,true,ms('--motion-base'));
    }
    current=next;
  }
  function decorate() {
    const selected=root.querySelector<HTMLElement>(targets.split(', ').map(s=>`${s}[aria-current="page"]`).join(', '));
    select(selected?geometry(selected):null);
    if (current) {
      draw(current.host.item,current.itemBox);current.host.item.node.hidden=false;
      if (current.groupBox) draw(current.host.group,current.groupBox);
    }
    for (const [node,ghost] of ghosts) {
      const target=geometry(ghost.target);
      const box=ghost.group?target?.groupBox:target?.itemBox;
      if (box) Object.assign(node.style,style(box));
    }
    if (hoverHeld && (media.matches || performance.now()>=hoverHeld.until)) hoverHeld=null;
    const hoverNode=hoverHeld?.node ?? (hovered!==current?.node?hovered:null);
    const hover=hoverNode?geometry(hoverNode):null;
    const focusNode=root.querySelector<HTMLElement>(targets.split(', ').map(s=>`${s}:focus-visible`).join(', '));
    const focus=focusNode?geometry(focusNode):null;
    for (const host of hosts.values()) {
      if (hover?.host===host) {
        if (lastHover && lastHover!==hoverNode && !host.hover.node.hidden) fadeOut(host.hover,lastHover,false,ms('--motion-fast'));
        if (host.hover.node.hidden) fadeIn(host.hover,hover.itemBox,ms('--motion-fast'));
        else draw(host.hover,hover.itemBox);
      } else if (!host.hover.node.hidden) fadeOut(host.hover,lastHover ?? host.node,false,ms('--motion-fast'));
      if (focus?.host===host) {draw(host.focus,focus.itemBox);host.focus.node.hidden=false;} else hide(host.focus);
    }
    lastHover=hoverNode;
  }
  function follow() {
    frame=0;if(disposed)return;
    discover();decorate();
    if (performance.now()<followUntil || root.hasAttribute('data-rail-motion-busy') || hoverHeld || ghosts.size) frame=requestAnimationFrame(follow);
  }
  function update() {
    followUntil=performance.now()+ms('--motion-wash');
    if (!frame) frame=requestAnimationFrame(follow);
  }
  function targetAt(node: EventTarget | null) {
    if (!(node instanceof Element)) return null;
    const control=node.closest<HTMLElement>(targets);
    if (control && root.contains(control)) return control;
    const identity = node.closest('[data-goal-status]')?.closest('.manager-card') ?? node.closest('.identity');
    return identity?.querySelector<HTMLElement>('.item-head') ?? null;
  }
  function pointer(event: PointerEvent) {
    if(event.pointerType==='touch'){hovered=null;hoverHeld=null;}else hovered=targetAt(event.target);
    update();
  }
  function preferenceChanged() {
    if (media.matches) {
      hoverHeld=null;
      for (const host of hosts.values()) for (const part of [host.group,host.item,host.hover,host.focus]) {part.animation?.cancel();part.fade?.cancel();part.animation=null;part.fade=null;part.arrival=0;}
      for (const [node,ghost] of ghosts) {ghost.animation.cancel();node.remove();} ghosts.clear();
    }
    update();
  }
  function leave() {hovered=null;update();}
  const observer=new MutationObserver(records=>{
    if(records.some(record=>!(record.target instanceof Element && record.target.closest('.rail-wash'))))update();
  });
  observer.observe(root,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['aria-current','class','style','data-rail-motion-busy']});
  const resize=new ResizeObserver(update);resize.observe(root);
  root.addEventListener('pointerover',pointer);root.addEventListener('pointerdown',pointer);
  root.addEventListener('pointerleave',leave);root.addEventListener('focusin',update);root.addEventListener('focusout',update);
  root.addEventListener('scroll',update,true);window.addEventListener('resize',update);media.addEventListener('change',preferenceChanged);
  discover();for(const host of hosts.values())resize.observe(host.node);update();
  return {destroy(){
    disposed=true;cancelAnimationFrame(frame);observer.disconnect();resize.disconnect();
    root.removeEventListener('pointerover',pointer);root.removeEventListener('pointerdown',pointer);root.removeEventListener('pointerleave',leave);
    root.removeEventListener('focusin',update);root.removeEventListener('focusout',update);root.removeEventListener('scroll',update,true);
    window.removeEventListener('resize',update);media.removeEventListener('change',preferenceChanged);
    for(const host of hosts.values())for(const part of [host.group,host.item,host.hover,host.focus]){part.animation?.cancel();part.fade?.cancel();part.node.remove();}
    for(const [node,ghost]of ghosts){ghost.animation.cancel();node.remove();}
  }};
}
