export function require_production_voice_io(): void {
  if (
    process.env.PLAN_TEST_MODE === '1' ||
    process.env.VITEST === 'true' ||
    process.env.PYTEST_CURRENT_TEST !== undefined
  )
    throw new Error('Production voice I/O is unavailable in tests. Inject recording adapters.');
}
