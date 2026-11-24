/** Prompt inputs reach the Mac message file store for every stored piece, images included. */
import { createHash } from 'node:crypto';
import { mkdtempSync, readFileSync, realpathSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterEach, describe, expect, test } from 'vitest';
import { ConversationMessageFiles } from '../../conversation/record/message_files.ts';
import type { ChildCommand, MessageContent } from '../wire.generated.ts';
import { ExecutorPaths } from './configuration.ts';
import { FilesService } from './files.ts';
import { MacInputs } from './inputs.ts';
import { MacCommandService } from './runner/service.ts';
import type { MacCommandDependencies } from './runner/command_contracts.ts';

const roots: string[] = [];
const services: FilesService[] = [];
afterEach(async () => {
  for (const service of services.splice(0)) {
    await service.stop({ deadline: performance.now() / 1000 + 10 });
    await service.settled();
  }
  for (const root of roots.splice(0)) rmSync(root, { recursive: true, force: true });
});

const child = {
  executor_incarnation: '11111111-1111-4111-8111-111111111111',
  child_id: 'child',
  conversation_id: 'c',
};
const image = { stored_file_id: 'f_image', media_type: 'image/png', file_name: 'shot.png' };
const file = {
  stored_file_id: 'f_file',
  media_type: 'text/plain',
  file_name: 'notes.txt',
  byte_count: 5,
};
function write_prompt(content: MessageContent, sender_content: MessageContent = []): ChildCommand {
  return {
    command_id: 'prompt',
    child,
    turn: { conversation_id: 'c', turn_number: 1 },
    operation: 'write_prompt',
    arguments: {
      kind: 'write_prompt',
      content,
      sender_content,
      sender_label: 'owner',
      mode: 'queue',
    },
  };
}
function steer(
  content: MessageContent,
  sender_content: MessageContent | null = null,
): ChildCommand {
  return {
    command_id: 'steer',
    child,
    turn: { conversation_id: 'c', turn_number: 1 },
    operation: 'steer',
    arguments: { kind: 'steer', content, sender_content, sender_label: 'owner' },
  };
}

function world() {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'mac-inputs-')));
  roots.push(root);
  const files = new FilesService();
  services.push(files);
  const paths = new ExecutorPaths(root);
  const served = new Map<string, Buffer>([
    ['f_image', Buffer.from('\x89PNG fixture bytes')],
    ['f_file', Buffer.from('notes')],
  ]);
  const requests: [string, string][] = [];
  const transport = {
    async download_command_file(command_id: string, stored_file_id: string, target: string) {
      requests.push([command_id, stored_file_id]);
      const bytes = served.get(stored_file_id);
      if (!bytes) throw new Error(`fixture does not serve ${stored_file_id}`);
      writeFileSync(target, bytes);
      return createHash('sha256').update(bytes).digest('hex');
    },
  };
  const failures: unknown[] = [];
  const inputs = new MacInputs(transport, files, paths, (error) => failures.push(error));
  // The adapters read inputs through the same store the Mac host builds.
  const store = new ConversationMessageFiles(paths.message_files_anchor);
  return { inputs, requests, served, store, failures };
}

describe('MacInputs.download_inputs', () => {
  test('an image in a write_prompt lands where the message file store reads it', async () => {
    const { inputs, requests, served, store, failures } = world();
    await inputs.download_inputs(
      write_prompt([{ text: 'look' }, image]),
      new AbortController().signal,
    );
    expect(requests).toEqual([['prompt', 'f_image']]);
    expect(readFileSync(store.path_of('c', 'f_image'))).toEqual(served.get('f_image'));
    expect(failures).toEqual([]);
  });

  test('an image in a steer is downloaded, from content and sender content', async () => {
    const { inputs, requests, served, store } = world();
    await inputs.download_inputs(steer([image], [image, file]), new AbortController().signal);
    expect(requests).toEqual([
      ['steer', 'f_image'],
      ['steer', 'f_file'],
    ]);
    expect(readFileSync(store.path_of('c', 'f_image'))).toEqual(served.get('f_image'));
    expect(readFileSync(store.path_of('c', 'f_file'))).toEqual(served.get('f_file'));
  });

  test('a text-only prompt downloads nothing', async () => {
    const { inputs, requests } = world();
    await inputs.download_inputs(
      write_prompt([{ text: 'hello' }], [{ text: 'hello' }]),
      new AbortController().signal,
    );
    await inputs.download_inputs(steer([{ text: 'more' }]), new AbortController().signal);
    expect(requests).toEqual([]);
  });
});

describe('MacCommandService input preparation', () => {
  function service() {
    const downloads: string[] = [];
    const refuse = (): never => {
      throw new Error('fixture native work is not reached');
    };
    const dependencies = {
      incarnation: 'inc',
      native_children: new Proxy({}, { get: refuse }),
      journal: { finish: async () => {}, mark_outputs_published: async () => {} },
      publication: { add: async () => {} },
      state: { admission_stopped: false, is_retiring: () => false, is_retired: () => false },
      asks: { get: () => null, take: () => null },
      inputs: {
        async download_inputs(command: ChildCommand) {
          downloads.push(command.command_id);
          throw new Error('fixture stops after input preparation');
        },
      },
      recheck_cleanup: refuse,
      stop_exact: refuse,
      now_ms: () => 0,
    } as unknown as MacCommandDependencies;
    return { commands: new MacCommandService(dependencies), downloads };
  }

  test('an image-only write_prompt or steer prepares its inputs', async () => {
    const { commands, downloads } = service();
    await commands.run(write_prompt([image]));
    await commands.run(steer([{ text: 'see' }], [image]));
    expect(downloads).toEqual(['prompt', 'steer']);
  });

  test('a text-only prompt prepares no inputs', async () => {
    const { commands, downloads } = service();
    await commands.run(write_prompt([{ text: 'hello' }], [{ text: 'hello' }]));
    expect(downloads).toEqual([]);
  });
});

// Gap: prompt transfer tests do not admit answer commands or resolve paths on the executor.
test('question answer files land in the executor store and resolve to its readable paths', async () => {
  const { inputs, requests, served, store } = world();
  const command: ChildCommand = {
    command_id: 'answer',
    child,
    turn: { conversation_id: 'c', turn_number: 1 },
    operation: 'answer_user_input',
    arguments: {
      kind: 'answer_user_input',
      request_id: 'question',
      answers: [{ question_id: 'proof', answers: [], attachments: [image, file] }],
    },
  };
  await inputs.download_inputs(command, new AbortController().signal);
  expect(requests).toEqual([
    ['answer', 'f_image'],
    ['answer', 'f_file'],
  ]);
  const { answer_file_references } = await import('../../conversation/user_input_attachments.ts');
  const { UserInputAnswer } = await import('../../conversation/events.ts');
  const refs = answer_file_references(
    [new UserInputAnswer({ question_id: 'proof', answers: [], attachments: [image, file] })],
    store,
    'c',
  );
  for (const piece of [image, file]) {
    const reference = store.path_of('c', piece.stored_file_id);
    expect(refs[0].answers[0]).toContain(reference);
    expect(readFileSync(reference)).toEqual(served.get(piece.stored_file_id));
  }
});
