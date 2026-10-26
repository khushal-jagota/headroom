import { expect, it } from 'vitest';
import { markdownContentIdentity, sameMarkdownIdentity, validCharacterOffset } from '../src/lib/markdown/textLocation';

it('identifies exact UTF-16 source revisions, including distinct lone surrogates', async () => {
  const original = await markdownContentIdentity('same\ud800source');
  expect(sameMarkdownIdentity(original, await markdownContentIdentity('same\ud800source'))).toBe(true);
  expect(sameMarkdownIdentity(original, await markdownContentIdentity('same\ud801source'))).toBe(false);
  expect(sameMarkdownIdentity(original, await markdownContentIdentity('same\ufffdsource'))).toBe(false);
  expect(sameMarkdownIdentity(original, await markdownContentIdentity('other source'))).toBe(false);
  expect(original.utf16Length).toBe(11);
});

it('accepts whole Unicode characters and rejects stale or split character offsets', () => {
  const text = 'a😀b';
  expect([0, 1, 3].map(offset => validCharacterOffset(text, offset))).toEqual([true, true, true]);
  for (const offset of [-1, 2, 4, 1.5, NaN]) expect(validCharacterOffset(text, offset)).toBe(false);
  expect(validCharacterOffset('', 0)).toBe(false);
});

it('keeps original sanitized child paths and block indices across group rendering', async () => {
  const { renderParsedMarkdown, parseMarkdown } = await import('../src/lib/markdownPipeline');
  const { vi } = await import('vitest');
  class ElementStub {
    className = '';
    children: object[] = [];
    appendChild(child: object) { this.children.push(child); }
    setAttribute() {}
  }
  vi.stubGlobal('HTMLImageElement', class {});
  vi.stubGlobal('document', {
    createElement: () => new ElementStub(),
    createTextNode: (data: string) => ({ data })
  });
  try {
    const document = parseMarkdown('first **bold** tail\n\nnext `code`');
    const complete: unknown[] = [];
    const grouped: unknown[] = [];
    renderParsedMarkdown(document, (node, block, path) => complete.push([node.data, block, path]));
    document.children.forEach((child, index) => {
      renderParsedMarkdown({ children: [child], definitions: document.definitions },
        (node, block, path) => grouped.push([node.data, block, path]), index);
    });
    expect(grouped).toEqual(complete);
    expect(complete).toContainEqual(['bold', 0, [1, 0]]);
    expect(complete).toContainEqual(['\n', 1, []]);
    expect(complete).toContainEqual(['code', 2, [1, 0]]);
  } finally { vi.unstubAllGlobals(); }
});
