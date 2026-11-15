import {fileURLToPath} from 'node:url';
import {prepare as prepareWorkspace} from './workspace-utility-pane.mjs';

export async function prepare(options) {
  // Reuse the full App/BoardRoute host and its controlled navigation transport.
  // A fresh browser context and this case's HTTP fixture own all goal responses.
  const workspace = await prepareWorkspace(options);
  return {...workspace, python:fileURLToPath(new URL('./goal-preview.py',import.meta.url))};
}
