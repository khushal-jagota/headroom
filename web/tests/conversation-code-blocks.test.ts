import { describe, expect, it } from 'vitest';
import { codeCopyText, tokenizeCode, type CodeToken } from '../src/lib/markdown/codeBlocks';

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
