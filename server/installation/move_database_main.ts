/** Moved-home entry: check an existing database with this admitted application. It never writes. */
import { isAbsolute } from 'node:path';
import { verifyDatabaseIntegrity } from '../core/database/index.ts';
import { require_application_schema } from '../core/database/admission.ts';

try {
  if (process.argv.length !== 3 || !isAbsolute(process.argv[2]!) || process.argv[2]!.includes('\0'))
    throw new Error('Expected one absolute moved home database path');
  verifyDatabaseIntegrity(process.argv[2]!);
  require_application_schema(process.argv[2]!);
} catch (error) {
  console.error(error);
  process.exitCode = 1;
}
