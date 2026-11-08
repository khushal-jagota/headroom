import {readFile} from 'node:fs/promises';
import {fileURLToPath} from 'node:url';
import {prepare as prepareWorkspace} from './workspace-utility-pane.mjs';

export async function prepare(options) {
  // Reuse the full App/BoardRoute host and its controlled navigation transport.
  // A fresh browser context and this case's HTTP fixture own all goal responses.
  const workspace = await prepareWorkspace(options);
  // Use the app font even when no other selected entry supplies these resources.
  const fonts = await Promise.all(['InterVariable.woff2', 'InterVariable-Italic.woff2'].map(async name => ({
    path: `/assets/fonts/${name}`, contentType: 'font/woff2',
    body: await readFile(new URL(`../../../../assets/fonts/${name}`, import.meta.url)),
  })));
  return {...workspace, resources:[...(workspace.resources ?? []), ...fonts], python:fileURLToPath(new URL('./goal-preview.py',import.meta.url))};
}
