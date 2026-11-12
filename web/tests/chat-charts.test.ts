import { expect, it } from 'vitest';
import { parseChatChart } from '../src/lib/markdown/charts/validation';
import { parseMarkdown } from '../src/lib/markdownPipeline';
import type { MarkdownTreeNode } from '../src/lib/markdown/contracts';

const spec = { kind: 'bar', series: [
  { name: '__proto__', points: [{ label: 'Week 1', value: -2.5 }, { label: 'Week 2', value: null }] },
  { name: 'target', points: [{ label: 'Week 1', value: 4 }] }
] };
const fence = (body = JSON.stringify(spec)) => '```panels-chart\n' + body + '\n```';
const charts = (nodes: MarkdownTreeNode[]): MarkdownTreeNode[] => nodes.flatMap(node => [
  ...(node.chart ? [node] : []), ...charts(node.children || [])
]);

it('keeps names literal and gives series safe independent identities', () => {
  const block = parseChatChart(JSON.stringify(spec))!;
  expect(block.series?.map(series => series.key)).toEqual(['actual', 'inline-1']);
  expect(block.series?.map(series => series.name)).toEqual(['__proto__', 'target']);
  expect(block.series?.[0].points.map(point => point.value)).toEqual([-2.5, null]);
});

it('rejects malformed, unsupported and renderer-unsafe nested specs', () => {
  const invalid: unknown[] = [null, [], { ...spec, kind: 'gauge' }, { ...spec, measure_id: 'x' },
    { ...spec, format: { decimals: 21 } }, { ...spec, format: { suffix: 4 } },
    { ...spec, series: [] }, { ...spec, series: [{ ...spec.series[0], color: 'red' }] },
    { ...spec, series: [{ name: 'A', points: [{ label: 'a', value: 2 }, { label: 'a', value: 3 }] }] },
    { ...spec, series: [{ name: 'A', points: [{ label: 'a', value: '2' }] }] },
    { ...spec, kind: 'line', stacked: true },
    { ...spec, series: [{ name: 'A', points: [{ label: 'a', value: -1e308 }, { label: 'b', value: 1e308 }] }] },
    { ...spec, stacked: true, series: ['A', 'B'].map(name => ({ name, points: [{ label: 'a', value: 1e308 }] })) }];
  for (const input of invalid) expect(parseChatChart(JSON.stringify(input))).toBeNull();
  expect(parseChatChart('{')).toBeNull();
  expect(parseChatChart(JSON.stringify(spec).replace('-2.5', '1e999'))).toBeNull();
});

it('requires an explicit matching fence before attaching trusted metadata', () => {
  const valid = charts(parseMarkdown(fence()).children);
  expect(valid).toHaveLength(1);
  expect(valid[0].chart?.source).toBe(fence());
  for (const source of [fence().slice(0, -4), fence().slice(0, -1), fence().replace(/```$/, '~~~'),
    fence('{'), fence().replace('panels-chart', 'json'), fence().replace('panels-chart', 'panels-chart extra')]) {
    expect(charts(parseMarkdown(source).children)).toHaveLength(0);
  }
});

it('keeps chart metadata separate from sanitized HTML and associates nested code blocks correctly', () => {
  const hostile = { ...spec, title: '<img src=x onerror=alert(1)>', unit: '<script>x</script>' };
  const source = '```text\nordinary\n```\n\n> ' + fence(JSON.stringify(hostile)).replaceAll('\n', '\n> ');
  const document = parseMarkdown(source);
  const found = charts(document.children);
  expect(found).toHaveLength(1);
  expect(found[0].chart?.block.title).toBe(hostile.title);
  expect(JSON.stringify(document.children)).not.toContain('"tagName":"img"');
  expect(found[0].properties).not.toHaveProperty('chart');
  const reposted = charts(parseMarkdown(found[0].chart!.source).children);
  expect(reposted).toHaveLength(1);
  expect(reposted[0].chart?.block).toEqual(found[0].chart?.block);
});
