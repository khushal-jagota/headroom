import { afterAll } from 'vitest';
import { clean_test_environment } from './clean_environment.ts';

// Vitest isolates worker environments from the global setup process.
afterAll(clean_test_environment());
