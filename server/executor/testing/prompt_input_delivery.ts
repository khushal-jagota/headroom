/** Reuse the authenticated local-link host: real HTTP and machine inputs, synthetic provider. */
import { createHash } from 'node:crypto';
import { readFileSync, realpathSync } from 'node:fs';
import { join } from 'node:path';
import { Readable } from 'node:stream';
import { expect, vi } from 'vitest';
import { ConversationMessageFiles } from '../../conversation/record/message_files.ts';
import type { BackendChild } from '../../conversation/backends/contracts.ts';
import { FifoLock } from '../../conversation/runtime/state.ts';
import type { VpsExecutor } from '../vps_transport.ts';
import type { ChildCommand, ChildReference } from '../wire.generated.ts';
import { ExecutorPaths } from '../mac/configuration.ts';
import { FilesService } from '../mac/files.ts';
import { MacInputs } from '../mac/inputs.ts';
import { MacCommandService } from '../mac/runner/service.ts';
import type { MacCommandDependencies } from '../mac/runner/command_contracts.ts';
import type { MacRunningChild } from '../mac/runner/contracts.ts';
import { MacFileTransport } from '../mac/transport/files.ts';
import type { MacHttpClient } from '../mac/transport/http.ts';

export async function prove_prompt_input_delivery(
  endpoint: VpsExecutor,
  client: MacHttpClient,
  reference: ChildReference,
  root: string,
): Promise<void> {
  const started = performance.now();
  const paths = new ExecutorPaths(join(realpathSync(root), 'prompt-machine'));
  const files = new FilesService();
  const transport = new MacFileTransport(client, files);
  const inputs = new MacInputs(transport, files, paths, (error) => {
    throw error;
  });
  const store = new ConversationMessageFiles(paths.message_files_anchor);
  const image_bytes = Buffer.from('screenshot fixture');
  const file_bytes = Buffer.from('attached notes');
  const image = await endpoint.message_files.keep(reference.conversation_id, image_bytes, {
    media_type: 'image/png',
  });
  const file = await endpoint.message_files.keep(reference.conversation_id, file_bytes, {
    media_type: 'text/plain',
  });
  const command: ChildCommand = {
    command_id: 'attached-prompt',
    child: reference,
    turn: { conversation_id: reference.conversation_id, turn_number: 1 },
    operation: 'write_prompt',
    arguments: {
      kind: 'write_prompt',
      content: [
        { text: 'Look at these attachments' },
        { stored_file_id: image.stored_file_id, media_type: 'image/png', file_name: 'shot.png' },
        {
          stored_file_id: file.stored_file_id,
          media_type: 'text/plain',
          file_name: 'notes.txt',
          byte_count: file_bytes.length,
        },
      ],
      sender_label: 'owner',
      sender_content: [],
      sender_message_count: 1,
      mode: 'queue',
    },
  };
  // Linux descriptor custody has separate VpsFiles coverage. Supply its admitted bytes
  // here so this HTTP/provider-boundary regression also runs on a native Mac host.
  const served = new Map([
    [image.stored_file_id, image_bytes],
    [file.stored_file_id, file_bytes],
  ]);
  const input_file = vi.spyOn(endpoint, 'input_file').mockImplementation(async (_, id, file_id) => {
    expect(id).toBe(command.command_id);
    const bytes = served.get(file_id)!;
    expect(bytes).toBeDefined();
    return {
      path: endpoint.message_files.path_of(reference.conversation_id, file_id),
      byte_count: bytes.length,
      sha256: createHash('sha256').update(bytes).digest('hex'),
      chunks: Readable.from([bytes]),
      close: async () => {},
    };
  });
  const write_prompt = vi.fn<BackendChild['write_prompt']>(async (turn, content) => {
    expect(turn.conversation_id).toBe(reference.conversation_id);
    expect(content).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ stored_file_id: image.stored_file_id }),
        expect.objectContaining({ stored_file_id: file.stored_file_id }),
        expect.objectContaining({ text: expect.stringContaining('Look at these attachments') }),
      ]),
    );
    // This is the provider boundary: files must already be readable before delivery.
    expect(readFileSync(store.path_of(reference.conversation_id, image.stored_file_id))).toEqual(
      image_bytes,
    );
    expect(readFileSync(store.path_of(reference.conversation_id, file.stored_file_id))).toEqual(
      file_bytes,
    );
    return { composed_content_delivered: true, agent_acceptance_proven: true };
  });
  const child = {
    reference,
    active_turn: null,
    lock: new FifoLock(),
    native: { write_prompt },
  } as unknown as MacRunningChild;
  const commands = new MacCommandService({
    incarnation: reference.executor_incarnation,
    native_children: { get: () => child, is_retiring: () => false },
    journal: { finish: async () => {}, mark_outputs_published: async () => {} },
    publication: { add: async () => {} },
    state: { admission_stopped: false, is_retiring: () => false, is_retired: () => false },
    inputs: {
      // Bound a regression's retry loop so the test reports refusal rather than hanging.
      download_inputs: (value: ChildCommand, signal: AbortSignal) =>
        inputs.download_inputs(value, AbortSignal.any([signal, AbortSignal.timeout(2000)])),
    },
    now_ms: Date.now,
  } as unknown as MacCommandDependencies);
  try {
    expect(await commands.run(command)).toMatchObject({ disposition: 'succeeded' });
    expect(write_prompt).toHaveBeenCalledOnce();
    expect(input_file).toHaveBeenCalledTimes(2);
  } finally {
    input_file.mockRestore();
    await files.stop({ deadline: performance.now() / 1000 + 5 });
    await files.settled();
    console.info('PROMPT_INPUT_BOUNDARY', {
      total_ms: performance.now() - started,
      extra_hosts: 0,
    });
  }
}
