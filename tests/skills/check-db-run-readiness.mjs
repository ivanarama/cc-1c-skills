#!/usr/bin/env node
// Run the focused Python readiness regression test from the common guard suite.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));
const python = process.env.PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const result = spawnSync(
  python,
  ['-X', 'utf8', join(HERE, 'test-db-run-readiness.py')],
  { stdio: 'inherit' },
);

if (result.error) {
  console.error(`db-run readiness guard failed to start Python: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);
