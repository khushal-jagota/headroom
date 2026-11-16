/** Hosted-only proof: distinct checkout paths, private products, one production cache. */
import { spawn } from "node:child_process";
import { mkdir, readFile, realpath, writeFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import { prepare_xcode_compilation_cache } from "../../server/executor/mac/runner/xcode_cache.ts";
import { write_project } from "./project.mts";
import { cache_diagnostics } from "./cache_diagnostics.mts";

await mkdir(resolve(process.argv[2]!), { recursive: true });
const root = await realpath(resolve(process.argv[2]!));
const evidence = resolve(process.argv[3]!);
await mkdir(evidence, { recursive: true });
const results: Record<string, unknown> = {};

async function run(
  command: string,
  args: string[],
  cwd: string,
  log_name: string,
  configuration?: string,
): Promise<{ seconds: number; output: string }> {
  const started = performance.now();
  const chunks: Buffer[] = [];
  let status: number | null;
  try {
    status = await new Promise<number | null>((complete, reject) => {
      const child = spawn(command, args, {
        cwd,
        env: {
          ...process.env,
          ...(configuration ? { XCODE_XCCONFIG_FILE: configuration } : {}),
        },
        stdio: ["ignore", "pipe", "pipe"],
      });
      child.stdout.on("data", (chunk: Buffer) => chunks.push(chunk));
      child.stderr.on("data", (chunk: Buffer) => chunks.push(chunk));
      child.once("error", reject);
      child.once("close", complete);
    });
  } finally {
    await writeFile(join(evidence, `${log_name}.log`), Buffer.concat(chunks));
  }
  const output = Buffer.concat(chunks).toString();
  if (status !== 0)
    throw new Error(
      `${command} ${log_name} failed (${status}). See ${log_name}.log`,
    );
  return { seconds: (performance.now() - started) / 1000, output };
}

async function build(name: string, expected: string) {
  const checkout = join(root, name);
  const ticket = join(root, "tickets", name);
  await mkdir(ticket, { recursive: true });
  const configuration = await prepare_xcode_compilation_cache(
    join(root, "executor"),
    ticket,
    checkout,
  );
  const args = [
    "-project",
    "Probe.xcodeproj",
    "-scheme",
    "Probe",
    "-configuration",
    "Debug",
    "-destination",
    "platform=macOS",
    "-derivedDataPath",
    join(checkout, ".artifacts", "DerivedData"),
    "-jobs",
    "4",
    "COMPILATION_CACHE_ENABLE_DIAGNOSTIC_REMARKS=YES",
  ];
  await run(
    "xcodebuild",
    [...args, "-showBuildSettings"],
    checkout,
    `${name}-settings`,
    configuration,
  );
  const built = await run(
    "xcodebuild",
    [...args, "build"],
    checkout,
    name,
    configuration,
  );
  const actual = await run(
    join(
      checkout,
      ".artifacts",
      "DerivedData",
      "Build",
      "Products",
      "Debug",
      "Probe",
    ),
    [],
    checkout,
    `${name}-output`,
  );
  if (actual.output.trim() !== expected)
    throw new Error(
      `${name}: expected ${expected}, got ${actual.output.trim()}`,
    );
  const result = { seconds: built.seconds, ...cache_diagnostics(built.output) };
  results[name] = result;
  return result;
}

try {
  results.toolchain = (
    await run("xcodebuild", ["-version"], root, "toolchain")
  ).output.trim();
  const repository = join(root, "fixture");
  await mkdir(repository);
  await write_project(repository);
  await run("git", ["init"], repository, "git-init");
  await run("git", ["add", "."], repository, "git-add");
  await run(
    "git",
    [
      "-c",
      "user.name=Cache Probe",
      "-c",
      "user.email=cache-probe@panels.invalid",
      "commit",
      "-m",
      "Xcode cache fixture",
    ],
    repository,
    "git-commit",
  );
  for (const name of ["cold", "warm", "concurrent-a", "concurrent-b"])
    await run(
      "git",
      ["clone", "--no-local", repository, join(root, name)],
      root,
      `${name}-clone`,
    );
  const cold = await build("cold", "baseline:496");
  const warm = await build("warm", "baseline:496");
  results.comparison = {
    cold_seconds: cold.seconds,
    warm_seconds: warm.seconds,
    saved_seconds: cold.seconds - warm.seconds,
    ratio: cold.seconds / warm.seconds,
  };
  for (const name of ["concurrent-a", "concurrent-b"]) {
    const source = join(root, name, "main.swift");
    await writeFile(
      source,
      (await readFile(source, "utf8")).replace("baseline:", `${name}:`),
    );
  }
  const concurrent = await Promise.allSettled([
    build("concurrent-a", "concurrent-a:496"),
    build("concurrent-b", "concurrent-b:496"),
  ]);
  const failed = concurrent.find((result) => result.status === "rejected");
  if (failed?.status === "rejected") throw failed.reason;
  if (warm.cache_hits.source === 0)
    throw new Error(
      "Second checkout has no target Swift cache hits. SDK module hits alone do not prove source reuse.",
    );
  results.success = true;
} catch (error) {
  results.success = false;
  results.error = error instanceof Error ? error.message : String(error);
  process.exitCode = 1;
} finally {
  await writeFile(
    join(evidence, "summary.json"),
    JSON.stringify(results, null, 2) + "\n",
  );
  console.log(JSON.stringify(results, null, 2));
}
