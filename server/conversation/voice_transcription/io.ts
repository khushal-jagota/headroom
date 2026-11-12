import { testModeFromEnvironment } from '../../core/config.ts';

export function require_production_voice_io(): void {
  if (
    testModeFromEnvironment() ||
    process.env.VITEST === 'true' ||
    process.env.PYTEST_CURRENT_TEST !== undefined
  )
    throw new Error('Production voice I/O is unavailable in tests. Inject recording adapters.');
}
