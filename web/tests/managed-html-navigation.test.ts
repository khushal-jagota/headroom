import { describe, expect, it } from "vitest";
import { resolveManagedHtmlNavigation } from "../src/lib/managedHtmlNavigation";

const origin = 'https://panels.example';
const root = '/files/tickets/t_owner/artifacts/site/index.html';
const child = '/files/tickets/t_owner/artifacts/site/nested/child.html';
const resolve = (href: string, current = root) => resolveManagedHtmlNavigation(href, current, root, origin);

describe('untrusted HTML navigation against a fixed directory', () => {
  it('normalizes before comparison and retains the original boundary after a child opens', () => {
    expect(resolve('quiet study.html')?.pathname).toBe('/files/tickets/t_owner/artifacts/site/quiet%20study.html');
    expect(resolve('nested/../study.html')?.pathname).toBe('/files/tickets/t_owner/artifacts/site/study.html');
    expect(resolve('../index.html#detail', child)?.hash).toBe('#detail');
    expect(resolve('nested/deeper/page.htm')?.pathname).toBe('/files/tickets/t_owner/artifacts/site/nested/deeper/page.htm');
    expect(resolve('../outside.html')).toBeNull();
    expect(resolve('/files/tickets/t_owner/artifacts/site-extra/page.html')).toBeNull();
    expect(resolve('../../outside.html', child)).toBeNull();
  });

  it('accepts absolute and scheme-relative URLs only at the same origin and inside that directory', () => {
    expect(resolve(`${origin}${child}`)?.pathname).toBe(child);
    expect(resolve(`//panels.example${child}`)?.pathname).toBe(child);
    for (const href of [`https://elsewhere.example${child}`, `//elsewhere.example${child}`, `${origin}/files/tickets/t_other/page.html`, `https://user@panels.example${child}`]) {
      expect(resolve(href), href).toBeNull();
    }
  });

  it('rejects ambiguous encodings, backslashes, queries, schemes and unsupported documents', () => {
    for (const href of ['nested%2fchild.html', 'nested%5cchild.html', '%252e%252e/out.html', '%252fout.html', 'nested\\child.html', '%2e%2e/out.html', 'study.html?download=1', 'javascript:alert(1)', 'data:text/html,x', 'blob:https://panels.example/id', 'notes.md', 'page.html\n', '%zz.html']) {
      expect(resolve(href), href).toBeNull();
    }
    expect(resolve('#detail', child)?.href).toBe(`${origin}${child}#detail`);
  });
});
