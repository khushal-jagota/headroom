import type { ViewReaderAnchor } from './anchorContracts';

/** Svelte proxies cannot cross structuredClone. Copy the local address fields explicitly. */
export function copyViewReaderAnchor(anchor: ViewReaderAnchor): ViewReaderAnchor {
  const content = anchor.content;
  return Object.freeze({
    key: anchor.key,
    order: Object.freeze({ sequence: anchor.order.sequence, slot: anchor.order.slot }),
    offset: anchor.offset,
    following: anchor.following,
    ...(content ? { content: Object.freeze({
      pieceIndex: content.pieceIndex,
      lineOffset: content.lineOffset,
      text: Object.freeze({
        contentIdentity: Object.freeze({ ...content.text.contentIdentity }),
        blockIndex: content.text.blockIndex,
        textLeafPath: Object.freeze([...content.text.textLeafPath]),
        utf16Offset: content.text.utf16Offset
      })
    }) } : {})
  });
}
