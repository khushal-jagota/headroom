import { expect, test } from 'vitest';
import { render_new_skill } from '../skills/logic.ts';
import type { WorkerTypeDefinition } from './contracts.ts';
import {
  convert_existing_skill,
  guidance_conversion_report,
  render_workflow_guide,
} from './guidance.ts';

const definition: WorkerTypeDefinition = {
  worker_type: 'probe',
  label: 'Probe',
  guidance: '',
  stages: [
    {
      id: 'needs_brief',
      label: 'Brief',
      gating_field: 'brief',
      is_terminal: false,
      ownership_mode: 'worker',
      instructions: '',
    },
    {
      id: 'needs_alpha',
      label: 'Alpha',
      gating_field: 'alpha',
      is_terminal: false,
      ownership_mode: 'worker',
      instructions: '',
    },
    {
      id: 'needs_beta',
      label: 'Beta',
      gating_field: 'beta',
      is_terminal: false,
      ownership_mode: 'user',
      instructions: '',
    },
    {
      id: 'needs_landing',
      label: 'Landing',
      gating_field: 'consequences',
      is_terminal: false,
      ownership_mode: 'worker',
      instructions: '',
    },
    {
      id: 'done',
      label: 'Done',
      gating_field: null,
      is_terminal: true,
      ownership_mode: null,
      instructions: '',
    },
  ],
  fields: [
    { id: 'brief', label: 'Brief' },
    { id: 'alpha', label: 'Alpha' },
    { id: 'beta', label: 'Beta' },
    { id: 'consequences', label: 'Consequences' },
  ],
  worker_profile: {
    specialist_skill: 'probe-worker',
    default_model: 'probe-model',
    default_reasoning_effort: 'probe-high',
    toolset_profile: 'default',
    default_backend: 'hermes',
  },
};

test('conversion accounts for exact prose and stage instructions', () => {
  const stage = definition.stages[0];
  const body = `# Owner instructions\n\nKeep every byte.\n\n## ${stage.label}\n\nDo exactly this.\n\n# Shared rules\n\nKeep these too.\n`;
  const source = `---\nname: probe-worker\ndescription: Probe\n---\n${body}`;
  const report = guidance_conversion_report(definition, source);
  expect(report.fragments.map((f) => f.text).join('')).toBe(body);
  expect(report.preserved_body_bytes).toBe(Buffer.byteLength(body));
  const converted = convert_existing_skill(definition, source);
  expect(converted.stages[0].instructions).toContain('Do exactly this.');
  expect(converted.guidance).not.toContain('Do exactly this.');
  expect(converted.guidance).toContain('Keep every byte.');
  expect(converted.guidance).toContain('Keep these too.');
  expect(
    report.fragments.every(
      (f) =>
        converted.guidance.includes(f.text) ||
        converted.stages.some((s) => s.instructions.includes(f.text)),
    ),
  ).toBe(true);
  expect(
    render_new_skill(
      converted.worker_profile.specialist_skill,
      'Probe',
      render_workflow_guide(converted),
    ),
  ).toContain(render_workflow_guide(converted));
  const changed = {
    ...converted,
    stages: converted.stages.map((s) =>
      s.id === stage.id ? { ...s, instructions: 'Updated' } : s,
    ),
  };
  expect(render_workflow_guide(changed)).not.toContain('Do exactly this.');
});

test('stage subtitles nested headings and fenced examples keep their owner', () => {
  const [first, second] = definition.stages;
  for (const separator of ['—', '–', '-']) {
    const escaped_id = first.id.replaceAll('_', '\\_');
    const body = `# Shared rules\n\n### ${escaped_id} ${separator} write the artifacts\n\nActual instructions.\n\n#### Evidence\n\nKeep nested detail in this stage.\n\n\`\`\`markdown\n### ${second.id} — this is only an example\n\`\`\`\n\n~~~markdown\n### Shared-looking example\n~~~\n\nKeep text after the example in this stage.\n\n### Working disciplines\n\nThis applies to every stage.\n### ${first.id}ly — not the stage\n\nThis is unmatched prose.\n`;
    const source = `---\nname: probe-worker\ndescription: Probe\n---\n${body}`;
    const report = guidance_conversion_report(definition, source);
    expect(report.fragments.map((f) => f.text).join('')).toBe(body);
    const converted = convert_existing_skill(definition, source),
      instructions = converted.stages[0].instructions;
    expect(instructions).toContain('Actual instructions.');
    expect(instructions).toContain('Keep nested detail in this stage.');
    expect(instructions).toContain('this is only an example');
    expect(instructions).toContain('Keep text after the example in this stage.');
    expect(instructions).not.toContain('This applies to every stage.');
    expect(converted.guidance).toContain('This applies to every stage.');
    expect(converted.guidance).toContain('This is unmatched prose.');
    expect(converted.stages[1].instructions).toBe('');
  }
});
