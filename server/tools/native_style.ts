/** Small checks for prohibited additions. Source review remains authoritative. */
import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

interface Finding {
  path: string;
  line: number;
  message: string;
}
const flock_owner = 'server/core/locks/flock.ts';
const removed_python_helpers =
  /\b(?:python_(?:(?:string_)?repr(?:_unicode)?|truth|sum|json_equal|os_error_message)|posix_child_path|os_errors|value_error)\b/;
const retired_python_error_class =
  /\b(?:ValueError|KeyError|PydanticSerializationError|CalledProcessError)\b/;
const fake_python_error_name =
  /(?:\.name\s*(?:={1,3}|!={1,2})|\bname\s*:)\s*['"](?:ValueError|RuntimeError|OSError|FileNotFoundError|PermissionError|NotADirectoryError|IsADirectoryError|JSONDecodeError|KeyError|IndexError|OverflowError|UnicodeDecodeError|UnicodeEncodeError|PydanticSerializationError|CalledProcessError|BlockingIOError)['"]/;

export function native_style_findings(diff: string): Finding[] {
  const findings: Finding[] = [];
  let path = '',
    line = 0;
  for (const entry of diff.split('\n')) {
    if (entry.startsWith('+++ b/')) {
      path = entry.slice(6);
      continue;
    }
    const hunk = /^@@ .* \+(\d+)(?:,\d+)? @@/.exec(entry);
    if (hunk) {
      line = Number(hunk[1]);
      continue;
    }
    if (!entry.startsWith('+')) {
      if (entry.startsWith(' ')) line++;
      continue;
    }
    const text = entry.slice(1),
      at = line++;
    if (!/^server\/.*\.[cm]?tsx?$/.test(path)) continue;
    const add = (message: string) => findings.push({ path, line: at, message });
    const imported = /^\s*(?:import\b|export\s*(?:\{|\*)|\}\s*from\b)/.test(text);
    if (imported && removed_python_helpers.test(text))
      add('Use native JavaScript values instead of removed Python helpers.');
    if (
      (imported || /^\s*(?:export\s+)?(?:default\s+)?class\s+/.test(text)) &&
      retired_python_error_class.test(text)
    )
      add('Use native errors instead of retired Python exception classes.');
    const wire_consumer = path.startsWith('server/executor/');
    if (
      imported &&
      !wire_consumer &&
      (/(?:\bpure_posix_path\b|['"][^'"]*\/python_path\.ts['"])/.test(text) ||
        (path !== 'server/execution_identity.ts' &&
          /['"][^'"]*\/(?:python_integer|whitespace)\.ts['"]/.test(text)))
    )
      add('Keep Python path, integer and whitespace compatibility at the executor wire boundary.');
    if (fake_python_error_name.test(text))
      add('Use native error classes instead of Python error-name assignments or branches.');
    if (imported && /['"]koffi(?:\/[^'"]*)?['"]/.test(text) && path !== flock_owner)
      add(`Keep the native binding in ${flock_owner}.`);
    const name =
      /^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\s+([\w$]+)/.exec(text)?.[1] ??
      (/=>|\bfunction\b/.test(text)
        ? /^\s*(?:export\s+)?(?:const|let)\s+([\w$]+)\s*=/.exec(text)?.[1]
        : undefined);
    if (
      name &&
      (removed_python_helpers.test(name) || (name === 'pure_posix_path' && !wire_consumer))
    )
      add('Do not recreate removed Python compatibility helpers.');
    const binding =
      /^\s*(?:export\s+)?(?:const|let)\s+[\w$]+\s*=\s*[\w$.]+\.func\(\s*['"][^'"]*\bflock\b/.test(
        text,
      );
    if (path !== flock_owner && (binding || (name && /flock/i.test(name))))
      add(`Use the shared flock helper in ${flock_owner}.`);
    if (name && /^(?:bounded|within|deadline|with_deadline|with_timeout)$/.test(name))
      add(
        'Use AbortSignal.timeout/any or the shared promise observer instead of another deadline helper.',
      );
  }
  return findings;
}

function main(base: string): void {
  const diff = execFileSync('git', ['diff', '--unified=0', '--no-ext-diff', base, '--', 'server'], {
    encoding: 'utf8',
  });
  const findings = native_style_findings(diff);
  for (const finding of findings)
    console.error(`${finding.path}:${finding.line}: ${finding.message}`);
  if (findings.length) process.exitCode = 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw new Error('Pass the pull request base commit.');
  main(process.argv[2]);
}
