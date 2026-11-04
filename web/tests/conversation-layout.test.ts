import {describe, expect, it} from 'vitest';
import {visibleHostBounds} from '../src/lib/conversation/layout/geometry';
import {visibleViewportFrom} from '../src/lib/keyboardViewport';

describe('conversation host geometry', () => {
  it('uses a narrow pane inside a wide viewport and intersects displaced keyboard bounds', () => {
    const host = {top: 40, left: 900, width: 380, height: 760};
    expect(visibleHostBounds(host, {top: 0, left: 0, width: 1440, height: 900}))
      .toEqual({top: 0, bottom: 0, left: 0, right: 0, height: 760, width: 380});
    expect(visibleHostBounds(host, {top: 120, left: 940, width: 320, height: 420}))
      .toEqual({top: 80, bottom: 260, left: 40, right: 20, height: 420, width: 320});
    expect(visibleHostBounds(host, {top: 900, left: 0, width: 1440, height: 200}).height).toBe(0);
  });

  it('ignores a keyboard-sized visual viewport once no text field has focus', () => {
    const keyboardUp = {top: 0, left: 0, width: 393, height: 470};
    const reading = {layoutWidth: 393, layoutHeight: 760, visual: keyboardUp};
    expect(visibleViewportFrom({...reading, textEntryFocused: true})).toEqual(keyboardUp);
    expect(visibleViewportFrom({...reading, textEntryFocused: false})).toEqual({top: 0, left: 0, width: 393, height: 760});
  });
});
