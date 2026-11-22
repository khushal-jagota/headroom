import { describe, expect, it } from 'vitest';
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from 'node:fs';
import { codeCopyText, codeLanguageName, tokenizeCode, type CodeToken } from '../src/lib/markdown/codeBlocks';
import { presentsCodeBlocks } from '../src/lib/managedMarkdown';

const words = (text: string, tokens: CodeToken[], kind: CodeToken['kind']) =>
  tokens.filter(token => token.kind === kind).map(token => text.slice(token.start, token.end));

describe('code block colour', () => {
  it('colours TypeScript without touching what it does not recognise', () => {
    const text = "// search\nexport function search(sql: ReadSql, query: string): Hit[] {\n  const MAX = 50; return sql.all(`SELECT 1`, 'x', true);\n}";
    const tokens = tokenizeCode(text, 'ts');
    expect(words(text, tokens, 'comment')).toEqual(['// search']);
    expect(words(text, tokens, 'keyword')).toEqual(['export', 'function', 'const', 'return']);
    expect(words(text, tokens, 'function')).toEqual(['search', 'all']);
    expect(words(text, tokens, 'type')).toEqual(['ReadSql', 'Hit']);
    expect(words(text, tokens, 'string')).toEqual(['`SELECT 1`', "'x'"]);
    expect(words(text, tokens, 'number')).toEqual(['50']);
    expect(words(text, tokens, 'literal')).toEqual(['true']);
  });

  it('keeps tokens in order, inside the text, and never overlapping', () => {
    const text = 'def run(x):\n    """doc\n    string"""\n    return x + 0x1f  # done\n';
    const tokens = tokenizeCode(text, 'python');
    for (let index = 0; index < tokens.length; index++) {
      expect(tokens[index].start).toBeLessThan(tokens[index].end);
      expect(tokens[index].end).toBeLessThanOrEqual(text.length);
      if (index > 0) expect(tokens[index].start).toBeGreaterThanOrEqual(tokens[index - 1].end);
    }
    expect(words(text, tokens, 'string')).toEqual(['"""doc\n    string"""']);
    expect(words(text, tokens, 'comment')).toEqual(['# done']);
    expect(words(text, tokens, 'number')).toEqual(['0x1f']);
  });

  it('reads SQL keywords in any case and diff lines by their mark', () => {
    const sql = 'SELECT id FROM t -- note\nwhere x = 1';
    expect(words(sql, tokenizeCode(sql, 'sql'), 'keyword')).toEqual(['SELECT', 'FROM', 'where']);
    const diff = '--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new\n same';
    const tokens = tokenizeCode(diff, 'diff');
    expect(words(diff, tokens, 'deleted')).toEqual(['-old']);
    expect(words(diff, tokens, 'inserted')).toEqual(['+new']);
  });

  it('leaves Rust lifetimes alone and unknown languages uncoloured', () => {
    const rust = "fn f<'a>(c: char) -> &'a str { let q = 'x'; }";
    expect(words(rust, tokenizeCode(rust, 'rust'), 'string')).toEqual(["'x'"]);
    expect(tokenizeCode('anything at all', null)).toEqual([]);
    expect(tokenizeCode('anything at all', 'brainfuck')).toEqual([]);
  });

  it('copies the code without the fence line break', () => {
    expect(codeCopyText('a\nb\n')).toBe('a\nb');
    expect(codeCopyText('a')).toBe('a');
  });
});

describe('code block header', () => {
  it('names a language the way a person says it, and leaves an unknown tag as written', () => {
    expect(codeLanguageName('ts')).toBe('TypeScript');
    expect(codeLanguageName('go')).toBe('Go');
    expect(codeLanguageName('sh')).toBe('Shell');
    expect(codeLanguageName('json')).toBe('JSON');
    expect(codeLanguageName('hcl')).toBe('hcl');
    expect(codeLanguageName(null)).toBe('');
  });
});

describe('artifacts and pages keep their own code blocks', () => {
  const host = (inConversation: boolean) => ({
    closest: (selector: string) => (inConversation && selector === '.chat-thread' ? ({} as Element) : null)
  });

  it('presents code blocks only in the conversation transcript', () => {
    expect(presentsCodeBlocks(host(true))).toBe(true);
    // An artifact preview, a file preview page, a Ticket: the <pre> is left as rendered.
    expect(presentsCodeBlocks(host(false))).toBe(false);
  });

  it('scopes every code block rule to the conversation', () => {
    const css = (readFileSync(new URL('../src/lib/markdown/codeBlocks.css', import.meta.url), 'utf8') as string)
      .replace(/\/\*[\s\S]*?\*\//g, '');
    const selectors = [...css.matchAll(/(^|})\s*([^{}@]+?)\s*\{/g)].map(match => match[2].trim())
      .filter(selector => selector !== ':root' && !selector.startsWith('::highlight'));
    expect(selectors.length).toBeGreaterThan(0);
    for (const selector of selectors) {
      for (const part of selector.split(',')) expect(part.trim(), selector).toMatch(/^\.chat-thread /);
    }
  });
});
