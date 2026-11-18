import { expect, test } from 'vitest';
import { scripted_manifest } from '../../drivers/testing/registry.ts';
import type { BackendIdentity } from './contracts.ts';
import { answer } from './catalogs/readers.ts';
import { identity } from './identity.ts';
import { probe_card } from './probe.ts';
import { RecordingBackendProbeEnvironment } from './recording_environment.ts';

// Gap: only the built-in drivers' cards were checked, and their sign-in comes from an identity
// command. An identity a catalog read supplies was merged after the shared sign-in wording, so a
// driver without an identity command had to write its own.
test('a sign-in state only the catalog read reports gets the shared card wording', async () => {
  const environment = new RecordingBackendProbeEnvironment();
  environment.executables.set('scripted', '/synthetic/scripted');
  const diagnoses = async (found: BackendIdentity) =>
    (
      await probe_card(
        {
          driver_kind: 'scripted',
          manifest: scripted_manifest('scripted'),
          read_identity: null,
          read_catalog: async () =>
            answer({ identity: found, diagnoses: ['The scripted catalog lists no models.'] }),
        },
        {
          environment,
          refresh: true,
          discover_update_advisory: false,
          now: new Date(0),
          model_version: () => [],
        },
      )
    ).diagnoses;
  const unreadable_version =
    '`scripted --version` did not report a version, so Panels cannot tell which one is installed.';
  expect(await diagnoses(identity('unauthenticated'))).toEqual([
    unreadable_version,
    '`scripted` is not signed in.',
    'The scripted catalog lists no models.',
  ]);
  expect(await diagnoses(identity('unknown'))).toEqual([
    unreadable_version,
    'Panels could not read who `scripted` is signed in as.',
    'The scripted catalog lists no models.',
  ]);
  expect(await diagnoses(identity('authenticated'))).toEqual([
    unreadable_version,
    'The scripted catalog lists no models.',
  ]);
});
