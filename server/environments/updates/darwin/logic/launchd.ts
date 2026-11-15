import type { DarwinHomeJob } from '../contracts.ts';

function block(text: string, name: string): string[] {
  const match = new RegExp('^\\s*' + name + ' = \\{\\n(.*?)^\\s*\\}', 'ms').exec(text);
  if (!match) throw new Error(`Loaded Mac job ${name} is unavailable`);
  return match[1]!
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
}
export function launchd_field(text: string, name: string): string | null {
  return new RegExp('^\\s*' + name + ' = (.+)$', 'm').exec(text)?.[1] ?? null;
}
export function authenticate_loaded_job(
  text: string,
  path: string,
  job: DarwinHomeJob,
  environment_keys: readonly string[],
  canonical: (path: string) => string = (value) => value,
): number | null {
  for (const [key, expected] of [
    ['path', path],
    ['program', job.ProgramArguments[0]],
    ['working directory', job.WorkingDirectory],
  ])
    if (
      key === 'path'
        ? canonical(launchd_field(text, key) ?? '') !== canonical(expected!)
        : launchd_field(text, key!) !== expected
    )
      throw new Error(`Loaded Mac job has a different ${key}`);
  const arguments_ = block(text, 'arguments');
  if (
    arguments_.length !== job.ProgramArguments.length ||
    arguments_.some((value, index) => value !== job.ProgramArguments[index])
  )
    throw new Error('Loaded Mac job has different arguments');
  const environment = block(text, 'environment');
  for (const key of environment_keys) {
    const value = environment.find(
      (line) => line.startsWith(key + ' => ') || line.startsWith(key + ' = '),
    );
    if (
      !value ||
      value.replace(new RegExp('^' + key + ' (?:=>|=) '), '') !== job.EnvironmentVariables[key]
    )
      throw new Error(`Loaded Mac job has a different ${key}`);
  }
  const raw = launchd_field(text, 'pid');
  if (raw === null) return null;
  const pid = Number(raw);
  if (!/^[1-9][0-9]*$/.test(raw) || !Number.isSafeInteger(pid))
    throw new Error('Loaded Mac job PID is invalid');
  return pid;
}
