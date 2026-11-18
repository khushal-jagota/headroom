import assert from 'node:assert/strict';
import { it } from 'vitest';
import { install_deadline_seconds } from './install_deadline.ts';

it('the install deadline counts the backup, the steps and a rollback restore', () => {
  // settle() and the Updating card read this deadline. Pure arithmetic.
  const live = 2_700_000_000; // 135 s at 20 MB/s
  assert.equal(
    install_deadline_seconds({ backup: false, steps: false, database_bytes: live }),
    900,
  );
  assert.equal(
    install_deadline_seconds({ backup: true, steps: false, database_bytes: live }),
    900 + 135,
  );
  assert.equal(
    install_deadline_seconds({ backup: true, steps: true, database_bytes: live }),
    900 + 135 + 900 + 135,
  );
  // A small database still gets the two-minute floor for each copy.
  assert.equal(
    install_deadline_seconds({ backup: false, steps: true, database_bytes: 1_000 }),
    900 + 900 + 120,
  );
});
