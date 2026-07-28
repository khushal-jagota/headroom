import {
  MOST_HELD_LINES,
  NEAR_NEWEST_LINE_PIXELS,
  NEWEST_LINE_BOTTOM_GAP_PIXELS,
  SENT_MESSAGE_TOP_GAP_PIXELS
} from "./viewportConfiguration";

export type HeldView = {
  scrollTop: number;
  lines: { element: Element; top: number }[];
};

export type ThreadReading = {
  newestLineBottomPixels: number;
  remainingReservedSpacePixels: number;
  newestLineIsInSight: boolean;
};

export type ThreadGeometry = {
  hasShape(): boolean;
  read(reservedSpacePixels: number): ThreadReading;
  newestLineIsInSight(cachedNewestLineBottomPixels: number): boolean;
  newestLineScrollTop(cachedNewestLineBottomPixels: number): number;
  sentMessage(messageId: string): Element | null;
  answerRoomMessage(): Element | null;
  answerRoomPixels(message: Element): number;
  sentMessageScrollTop(message: Element): number;
  holdView(): HeldView;
  restoredScrollTop(held: HeldView): number | null;
};

/** A live, read-only view of where things are in one conversation thread. */
export function threadGeometry(
  thread: HTMLDivElement,
  reservedSpaceElement: HTMLDivElement | null
): ThreadGeometry {
  function topWithin(element: Element): number {
    return (
      element.getBoundingClientRect().top
      - thread.getBoundingClientRect().top
      + thread.scrollTop
    );
  }

  /** Everything that takes up room, looking through display: contents wrappers. */
  function contentElements(): Element[] {
    const laidOut: Element[] = [];
    const consider = (element: Element): void => {
      if (element === reservedSpaceElement) return;
      if (element.getClientRects().length > 0) {
        laidOut.push(element);
        return;
      }
      for (const inside of element.children) consider(inside);
    };
    for (const child of thread.children) consider(child);
    return laidOut;
  }

  function newestLineBottom(): number {
    const content = contentElements();
    const last = content[content.length - 1];
    return last === undefined ? 0 : topWithin(last) + last.getBoundingClientRect().height;
  }

  function newestLineScrollTop(cachedNewestLineBottomPixels: number): number {
    return (
      cachedNewestLineBottomPixels
      + NEWEST_LINE_BOTTOM_GAP_PIXELS
      - thread.clientHeight
    );
  }

  function newestLineIsInSight(cachedNewestLineBottomPixels: number): boolean {
    const beyondTheFold =
      newestLineScrollTop(cachedNewestLineBottomPixels) - thread.scrollTop;
    if (beyondTheFold > NEAR_NEWEST_LINE_PIXELS) return false;
    return cachedNewestLineBottomPixels >= thread.scrollTop;
  }

  return {
    hasShape(): boolean {
      return thread.clientHeight > 0;
    },

    read(reservedSpacePixels: number): ThreadReading {
      const newestLineBottomPixels = newestLineBottom();
      const alwaysBelowTheLastLine =
        thread.scrollHeight - newestLineBottomPixels - reservedSpacePixels;
      const stillHolding = Math.max(
        0,
        Math.ceil(
          thread.scrollTop
          + thread.clientHeight
          - newestLineBottomPixels
          - alwaysBelowTheLastLine
        )
      );
      const remainingReservedSpacePixels = Math.min(
        reservedSpacePixels,
        stillHolding
      );
      return {
        newestLineBottomPixels,
        remainingReservedSpacePixels,
        newestLineIsInSight: newestLineIsInSight(newestLineBottomPixels)
      };
    },

    newestLineIsInSight,
    newestLineScrollTop,

    sentMessage(messageId: string): Element | null {
      const drawn = Array.from(
        thread.querySelectorAll<HTMLElement>("[data-conversation-outgoing]")
      ).find((element) => element.dataset.conversationOutgoing === messageId);
      if (drawn !== undefined) return drawn;
      const recorded = thread.querySelectorAll('[data-conversation-row="prompt"]');
      return recorded[recorded.length - 1] ?? null;
    },

    answerRoomMessage(): Element | null {
      const drawn = thread.querySelectorAll("[data-conversation-outgoing]");
      const recorded = thread.querySelectorAll('[data-conversation-row="prompt"]');
      return drawn[drawn.length - 1] ?? recorded[recorded.length - 1] ?? null;
    },

    answerRoomPixels(message: Element): number {
      return Math.max(
        0,
        Math.ceil(
          thread.clientHeight
          - message.getBoundingClientRect().height
          - SENT_MESSAGE_TOP_GAP_PIXELS
        )
      );
    },

    sentMessageScrollTop(message: Element): number {
      return Math.max(0, topWithin(message) - SENT_MESSAGE_TOP_GAP_PIXELS);
    },

    holdView(): HeldView {
      const lines: HeldView["lines"] = [];
      for (const element of contentElements()) {
        const top = topWithin(element);
        if (top + element.getBoundingClientRect().height <= thread.scrollTop) continue;
        lines.push({ element, top });
        if (lines.length === MOST_HELD_LINES) break;
      }
      return { scrollTop: thread.scrollTop, lines };
    },

    restoredScrollTop(held: HeldView): number | null {
      const survivor = held.lines.find((line) => thread.contains(line.element));
      if (survivor === undefined) return null;
      return Math.max(
        0,
        held.scrollTop + topWithin(survivor.element) - survivor.top
      );
    }
  };
}
