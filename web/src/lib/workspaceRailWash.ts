/** Rail decoration only. Selection and interaction remain owned by the existing controls. */
type Box = { left: number; top: number; width: number; height: number; radius?: string };
type Layer = { node: HTMLDivElement; box: string; animation: Animation | null; fade: Animation | null; arrival: number };
type Host = { node: HTMLElement; origin: HTMLDivElement; item: Layer; hover: Layer; focus: Layer };
// `group` is the Manager block the row sits in: moving within one block glides the fill.
type Target = { node: HTMLElement; host: Host; group: HTMLElement | null; itemBox: Box };
const properties = ['left', 'top', 'width', 'height'] as const;
/* Each row is one element. The wash draws that element's box, and the same element owns
 * the row's hover, click and preview, so the washed area and the area that responds are
 * one area. Selection and keyboard focus are marked on a control inside some rows. */
const rows = '.identity, .worker-row, .goal, .settled-row, .utility';
const marks = '.item-head, .worker-row, .goal, .settled-title, .utility';
const rowOf = (mark: HTMLElement | null) => mark?.closest<HTMLElement>(rows) ?? null;

export function workspaceRailWash(root: HTMLElement) {
  const hosts = new Map<HTMLElement, Host>();
  const ghosts = new Map<HTMLElement, { target: HTMLElement; animation: Animation }>();
  const media = matchMedia('(prefers-reduced-motion: reduce)');
  let current: Target | null = null;
  let hovered: HTMLElement | null = null;
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
  /* Where the host's layers are placed from: a zero-size mark at the host's padding-box origin,
   * which scrolls with its content. Measuring from it (not from the host's border box) keeps
   * every fill on its row whatever gutter the host keeps for a scrollbar, on either side. */
  function originOf(host: HTMLElement): HTMLDivElement {
    const node = document.createElement('div');
    node.className = 'rail-wash-origin';
    node.setAttribute('aria-hidden', 'true');
    node.style.cssText = 'position:absolute;left:0;top:0;width:0;height:0;pointer-events:none;';
    host.prepend(node);
    return node;
  }
  function discover() {
    for (const node of root.querySelectorAll<HTMLElement>('.active-work-scroll, .settled-body, .rail-utilities')) {
      if (!hosts.has(node)) { hosts.set(node, {node, origin: originOf(node), item:layer(node,'item'), hover:layer(node,'hover'), focus:layer(node,'focus')}); resize.observe(node); }
    }
    for (const node of root.querySelectorAll('.manager-card, .identity, .worker-row, .goal, .settled-row')) { if (!observed.has(node)) {observed.add(node);resize.observe(node);} }
    for (const node of observed) if (!root.contains(node)) {resize.unobserve(node);observed.delete(node);}
    for (const [node,host] of hosts) if (!root.contains(node)) {
      resize.unobserve(node);
      for (const part of [host.item,host.hover,host.focus]) {part.animation?.cancel();part.fade?.cancel();part.node.remove();}
      host.origin.remove();
      hosts.delete(node);
    }
  }
  function geometry(node: HTMLElement): Target | null {
    const hostNode = node.closest<HTMLElement>('.active-work-scroll, .settled-body, .rail-utilities');
    const host = hostNode && hosts.get(hostNode);
    if (!host || !root.contains(node) || !node.getClientRects().length) return null;
    const base = host.origin.getBoundingClientRect();
    const local = (rect: DOMRect): Box => ({left:rect.left-base.left,top:rect.top-base.top,width:rect.width,height:rect.height});
    // Only the open item is lit; the Manager you are inside has no mark of its own (owner).
    const group = node.closest<HTMLElement>('.manager-card');
    // Every fill is its row's own box with the row's own radius. The node is the row itself,
    // the same element that owns its hover, click and preview: a Manager's header is its
    // project eyebrow, its name and a sole goal's line together, as one block.
    const itemBox: Box = {...local(node.getBoundingClientRect()), radius:getComputedStyle(node).borderRadius};
    return {node,host,group,itemBox};
  }
  const style = (box: Box) => Object.fromEntries(properties.map(key=>[key,`${box[key]}px`]));
  function draw(part: Layer, box: Box, travel = false) {
    part.node.style.borderRadius = box.radius ?? '';
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
  function fadeOut(part: Layer, target: HTMLElement, duration: number) {
    if (part.node.hidden) return;
    if (!media.matches) {
      const ghost = part.node.cloneNode(true) as HTMLElement;
      for (const key of properties) ghost.style[key]=getComputedStyle(part.node)[key];
      ghost.classList.add('rail-wash--leaving');
      part.node.after(ghost);
      const animation=ghost.animate([{opacity:getComputedStyle(part.node).opacity},{opacity:0}],{duration,easing:'ease'});
      ghosts.set(ghost,{target,animation});
      animation.onfinish=()=>{ghost.remove();ghosts.delete(ghost);};
    }
    hide(part);
  }
  const identity = (node: HTMLElement) => node.dataset.railMotion ?? node.dataset.goalId ?? node.getAttribute('href') ?? node.closest<HTMLElement>('[data-settled-item]')?.dataset.settledItem ?? (node.matches('.identity') ? node.closest<HTMLElement>('[data-rail-motion]')?.dataset.railMotion : undefined);
  function select(next: Target | null) {
    // A bucket move recreates a keyed row in a different bucket. It is still the same selection.
    if (current && next && current.host===next.host && identity(current.node) && identity(current.node)===identity(next.node)) {
      if (current.node!==next.node) {current.host.item.arrival=0;current.host.item.animation?.cancel();current.host.item.animation=null;}
      current=next;return;
    }
    if (current?.node === next?.node && current?.host === next?.host && current?.group === next?.group) {current=next;return;}
    const prev=current;
    // A click on the row under the pointer: the fills are see-through, so the hover fill and the
    // selected fill must never overlap (they would add up to a brighter flash). The hover fill
    // goes at once and the selected fill lands on the row at once, with no fade or travel.
    const clicked=Boolean(next && hovered === next.node);
    if (clicked) {hovered=null;lastHover=null;hide(next!.host.hover);}
    const sameHost=prev && next && prev.host===next.host;
    const sameGroup=sameHost && prev.group && prev.group===next.group;
    const deepen=sameHost && prev.group && next.group && prev.group!==next.group;
    if (prev && !sameGroup) fadeOut(prev.host.item,prev.node,ms(deepen?'--motion-wash':'--motion-base'));
    if (next) {
      if (clicked) {hide(next.host.item);draw(next.host.item,next.itemBox);next.host.item.node.hidden=false;}
      else if (sameGroup) draw(next.host.item,next.itemBox,true);
      else fadeIn(next.host.item,next.itemBox,ms(deepen?'--motion-wash':'--motion-slow'));
    }
    current=next;
  }
  function decorate() {
    const selected=rowOf(root.querySelector<HTMLElement>(marks.split(', ').map(s=>`${s}[aria-current="page"]`).join(', ')));
    select(selected?geometry(selected):null);
    if (current) {
      draw(current.host.item,current.itemBox);current.host.item.node.hidden=false;
    }
    for (const [node,ghost] of ghosts) {
      const target=geometry(ghost.target);
      if (target) Object.assign(node.style,style(target.itemBox));
    }
    const hoverNode=hovered!==current?.node?hovered:null;
    const hover=hoverNode?geometry(hoverNode):null;
    const focusNode=rowOf(root.querySelector<HTMLElement>(marks.split(', ').map(s=>`${s}:focus-visible`).join(', ')));
    const focus=focusNode?geometry(focusNode):null;
    for (const host of hosts.values()) {
      if (hover?.host===host) {
        if (lastHover && lastHover!==hoverNode && !host.hover.node.hidden) fadeOut(host.hover,lastHover,ms('--motion-fast'));
        if (host.hover.node.hidden) fadeIn(host.hover,hover.itemBox,ms('--motion-fast'));
        else draw(host.hover,hover.itemBox);
      } else if (!host.hover.node.hidden) fadeOut(host.hover,lastHover ?? host.node,ms('--motion-fast'));
      // The layer takes the control's own box; its outline stands the focus offset clear of it.
      if (focus?.host===host) {draw(host.focus,focus.itemBox);host.focus.node.hidden=false;} else hide(host.focus);
    }
    lastHover=hoverNode;
  }
  function follow() {
    frame=0;if(disposed)return;
    discover();decorate();
    if (performance.now()<followUntil || root.hasAttribute('data-rail-motion-busy') || ghosts.size) frame=requestAnimationFrame(follow);
  }
  function update() {
    followUntil=performance.now()+ms('--motion-wash');
    if (!frame) frame=requestAnimationFrame(follow);
  }
  function targetAt(node: EventTarget | null) {
    if (!(node instanceof Element)) return null;
    const row=node.closest<HTMLElement>(rows);
    return row && root.contains(row) ? row : null;
  }
  function pointer(event: PointerEvent) {
    if(event.pointerType==='touch')hovered=null;else hovered=targetAt(event.target);
    update();
  }
  function preferenceChanged() {
    if (media.matches) {
      for (const host of hosts.values()) for (const part of [host.item,host.hover,host.focus]) {part.animation?.cancel();part.fade?.cancel();part.animation=null;part.fade=null;part.arrival=0;}
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
    for(const host of hosts.values()){host.origin.remove();for(const part of [host.item,host.hover,host.focus]){part.animation?.cancel();part.fade?.cancel();part.node.remove();}}
    for(const [node,ghost]of ghosts){ghost.animation.cancel();node.remove();}
  }};
}
