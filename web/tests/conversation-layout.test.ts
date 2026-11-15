import {afterEach, describe, expect, it, vi} from 'vitest';
import {visibleHostBounds} from '../src/lib/conversation/layout/geometry';
import {visibleViewportFrom, visualViewportClientRect} from '../src/lib/keyboardViewport';

describe('conversation host geometry', () => {
  it('uses a narrow pane inside a wide viewport and intersects displaced keyboard bounds', () => {
    const host = {top: 40, left: 900, width: 380, height: 760};
    expect(visibleHostBounds(host, {top: 0, left: 0, width: 1440, height: 900}))
      .toEqual({top: 0, bottom: 0, left: 0, right: 0, height: 760, width: 380});
    expect(visibleHostBounds(host, {top: 120, left: 940, width: 320, height: 420}))
      .toEqual({top: 80, bottom: 260, left: 40, right: 20, height: 420, width: 320});
    expect(visibleHostBounds(host, {top: 900, left: 0, width: 1440, height: 200}).height).toBe(0);
  });

  it('retains menu bounds across focus transfer and restores the viewport after dismissal', () => {
    const keyboardUp = {top: 0, left: 0, width: 393, height: 470};
    const reading = {layoutWidth: 393, layoutHeight: 760, visual: keyboardUp};
    expect(visibleViewportFrom({...reading, textEntryFocused: true})).toEqual(keyboardUp);
    expect(visibleViewportFrom({...reading, textEntryFocused: false}, true)).toEqual(keyboardUp);
    expect(visibleViewportFrom({...reading, textEntryFocused: false})).toEqual({top: 0, left: 0, width: 393, height: 760});
  });

  afterEach(() => vi.unstubAllGlobals());

  it('places the visual viewport in element coordinates when iPhone Safari scrolls a field into view', () => {
    // Recorded in iOS 27 Simulator Safari with the keyboard up: Safari scrolled the page
    // 330 px, offsetTop reads 330, and element rectangles start at the visible top.
    vi.stubGlobal('window', {visualViewport: {offsetTop: 330, offsetLeft: 0, pageTop: 330, pageLeft: 0, width: 402, height: 384}});
    vi.stubGlobal('document', {documentElement: {getBoundingClientRect: () => ({top: -330, left: 0})}});
    expect(visualViewportClientRect()).toEqual({top: 0, left: 0, width: 402, height: 384});
    // Chromium measures element rectangles from the layout viewport, as offsetTop is.
    vi.stubGlobal('window', {visualViewport: {offsetTop: 120, offsetLeft: 0, pageTop: 520, pageLeft: 0, width: 390, height: 420}});
    vi.stubGlobal('document', {documentElement: {getBoundingClientRect: () => ({top: -400, left: 0})}});
    expect(visualViewportClientRect()).toEqual({top: 120, left: 0, width: 390, height: 420});
  });
});
