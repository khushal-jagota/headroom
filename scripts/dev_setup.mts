/** Reuse built development dependencies while keeping checkout source and writable caches local. */
import { createHash } from "node:crypto";
import { execFileSync, spawn } from "node:child_process";
import {
  chmod,
  lstat,
  mkdir,
  readFile,
  readdir,
  readlink,
  realpath,
  rename,
  rm,
  symlink,
  writeFile,
} from "node:fs/promises";
import { userInfo } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const tree = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const marker = ".panels-cache.json";
const ready = ".ready";
export const cache_environment = "PANELS_DEPENDENCY_CACHE";

const pip_registry = new Set([
  "PIP_INDEX_URL",
  "PIP_EXTRA_INDEX_URL",
  "PIP_TRUSTED_HOST",
  "PIP_CERT",
  "PIP_CLIENT_CERT",
  "PIP_TIMEOUT",
  "PIP_RETRIES",
  "PIP_PROXY",
]);
function install_environment(): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    NODE_ENV: "development",
    PIP_CONFIG_FILE: "/dev/null",
  };
  for (const name of Object.keys(environment))
    if (
      name.toUpperCase().startsWith("PIP_") &&
      name !== "PIP_CONFIG_FILE" &&
      !pip_registry.has(name)
    )
      delete environment[name];
  return environment;
}
const npm_install_options = [
  "--include=dev",
  "--include=optional",
  "--ignore-scripts=false",
  "--bin-links=true",
  "--global=false",
];

async function run(
  command: string,
  args: string[],
  cwd: string,
): Promise<void> {
  await new Promise<void>((complete, reject) => {
    const child = spawn(command, args, {
      cwd,
      stdio: "inherit",
      env: install_environment(),
    });
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0 ? complete() : reject(new Error(`${command} exited: ${code}`)),
    );
  });
}

function output(command: string, args: string[], cwd: string): string {
  return execFileSync(command, args, {
    cwd,
    encoding: "utf8",
    env: install_environment(),
  }).trim();
}
async function exists(path: string): Promise<boolean> {
  try {
    await lstat(path);
    return true;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === "ENOENT") return false;
    throw error;
  }
}
export function dependency_key(parts: string[]): string {
  const hash = createHash("sha256");
  for (const part of parts) hash.update(`${Buffer.byteLength(part)}:${part}`);
  return hash.digest("hex");
}

/** Python supplies the Unix file-lock primitive; TypeScript owns the build and its lifetime. */
async function acquire_lock(path: string): Promise<() => Promise<void>> {
  const child = spawn(
    "python3",
    [
      "-I",
      "-c",
      'import fcntl,sys\nf=open(sys.argv[1],"a")\nfcntl.flock(f,fcntl.LOCK_EX)\nprint("locked",flush=True)\nsys.stdin.read()',
      path,
    ],
    { stdio: ["pipe", "pipe", "inherit"] },
  );
  const closed = new Promise<void>((resolve_closed, reject) => {
    child.once("error", reject);
    child.once("exit", (code) =>
      code === 0
        ? resolve_closed()
        : reject(new Error(`Cache lock exited: ${code}`)),
    );
  });
  await new Promise<void>((resolve_locked, reject) => {
    child.stdout.once("data", () => resolve_locked());
    child.once("error", reject);
    child.once("exit", () =>
      reject(new Error("Cache lock closed before acquisition")),
    );
  });
  return async () => {
    child.stdin.end();
    await closed;
  };
}

export async function cached_directory(
  root: string,
  key: string,
  build: (path: string) => Promise<void>,
): Promise<string> {
  await mkdir(root, { recursive: true });
  const destination = join(root, key);
  const unlock = await acquire_lock(join(root, `${key}.lock`));
  try {
    if (await exists(join(destination, ready))) return destination;
    // This is one owned, unpublished cache entry, never a checkout or release directory.
    await rm(destination, { recursive: true, force: true });
    await mkdir(destination);
    try {
      await build(destination);
      await writeFile(join(destination, ".ready.tmp"), "ready\n");
      await rename(join(destination, ".ready.tmp"), join(destination, ready));
    } catch (error) {
      await rm(destination, { recursive: true, force: true });
      throw error;
    }
    return destination;
  } finally {
    await unlock();
  }
}

interface PythonInputs {
  runtime: string[];
  build_tools: string[];
  packages: string;
}
function python_inputs(checkout: string, python: string): PythonInputs {
  return JSON.parse(
    output(
      python,
      [
        "-I",
        "-c",
        "import json,sys,sysconfig,platform,tomllib,pathlib\n" +
          "p=tomllib.loads(pathlib.Path(sys.argv[1]).read_text())\n" +
          'print(json.dumps({"runtime":[sys.executable,str(sys.version_info[:3]),sys.implementation.name,str(sysconfig.get_config_var("SOABI")),platform.platform(),platform.machine()],"build_tools":p["build-system"]["requires"],"packages":sysconfig.get_path("purelib")}))',
        join(checkout, "pyproject.toml"),
      ],
      checkout,
    ),
  ) as PythonInputs;
}
const shell_quote = (value: string): string =>
  `'${value.replaceAll("'", "'\\''")}'`;

export async function python_environment(
  checkout: string,
  cache: string,
): Promise<void> {
  const inputs = python_inputs(checkout, "python3");
  const key = dependency_key([
    "python-v2",
    await readFile(join(checkout, "requirements.txt"), "utf8"),
    JSON.stringify(inputs.build_tools),
    JSON.stringify(inputs.runtime),
  ]);
  const dependencies = await cached_directory(
    cache,
    key,
    async (destination) => {
      await run("python3", ["-I", "-m", "venv", destination], checkout);
      await run(
        join(destination, "bin/python"),
        [
          "-I",
          "-m",
          "pip",
          "install",
          "-r",
          join(checkout, "requirements.txt"),
          ...inputs.build_tools,
        ],
        checkout,
      );
    },
  );
  const local = join(checkout, ".venv");
  await run("python3", ["-I", "-m", "venv", "--without-pip", local], checkout);
  const python = join(local, "bin/python");
  const local_packages = python_inputs(checkout, python).packages;
  const shared_packages = python_inputs(
    checkout,
    join(dependencies, "bin/python"),
  ).packages;
  await writeFile(
    join(local_packages, "development-dependencies.pth"),
    `import site; site.addsitedir(${JSON.stringify(shared_packages)})\n`,
  );
  for (const name of await readdir(join(dependencies, "bin"))) {
    if (
      name.startsWith("python") ||
      name.startsWith("activate") ||
      name === "Activate.ps1"
    )
      continue;
    const original = join(dependencies, "bin", name);
    const data = await readFile(original);
    const lines = data.toString("utf8").split("\n");
    let body: string | undefined;
    if (lines[0]?.startsWith("#!") && lines[0].includes("python"))
      body = lines.slice(1).join("\n");
    else if (lines[0] === "#!/bin/sh" && lines[1]?.startsWith("'''exec'"))
      body = lines.slice(3).join("\n");
    const destination = join(local, "bin", name);
    if (body !== undefined) {
      await writeFile(
        destination,
        `#!/bin/sh\n'''exec' ${shell_quote(python)} "$0" "$@"\n' '''\n${body}`,
      );
      await chmod(destination, 0o755);
    } else await symlink(original, destination);
  }
  await run(
    python,
    [
      "-I",
      "-m",
      "pip",
      "install",
      "--no-deps",
      "--no-build-isolation",
      "--editable",
      checkout,
      "--config-settings",
      "editable_mode=compat",
    ],
    checkout,
  );
}

export async function link_node_modules(
  source: string,
  destination: string,
): Promise<void> {
  await mkdir(destination);
  for (const name of await readdir(source)) {
    // Vite and Vitest keep checkout-specific state in these directories.
    if (name === ".vite" || name === ".vite-temp") continue;
    await symlink(join(source, name), join(destination, name));
  }
  await writeFile(join(destination, marker), JSON.stringify({ source }));
}

/** npm replaces only an owned local link tree, never the cache payload behind its links. */
export async function detach_node_modules(destination: string): Promise<void> {
  if (!(await exists(join(destination, marker)))) return;
  if (
    !(await lstat(destination)).isDirectory() ||
    (await realpath(destination)) !== resolve(destination)
  )
    throw new Error(
      `Refuse to detach a redirected dependency directory: ${destination}`,
    );
  const { source } = JSON.parse(
    await readFile(join(destination, marker), "utf8"),
  ) as { source: string };
  for (const name of await readdir(destination)) {
    const path = join(destination, name);
    if (
      (await lstat(path)).isSymbolicLink() &&
      (await readlink(path)) !== join(source, name)
    )
      throw new Error(
        `Refuse to detach an unrecognized dependency link: ${path}`,
      );
  }
  await rm(destination, { recursive: true });
}

export async function node_environment(
  checkout: string,
  cache: string,
  name: string,
): Promise<void> {
  const package_root = join(checkout, name);
  const metadata = await readFile(join(package_root, "package.json"), "utf8");
  const node = JSON.stringify([
    process.versions.modules,
    process.platform,
    process.arch,
  ]);
  const lock = await readFile(join(package_root, "package-lock.json"), "utf8");
  const key = dependency_key(["node-v2", metadata, lock, node]);
  const dependencies = await cached_directory(
    cache,
    key,
    async (destination) => {
      await writeFile(join(destination, "package.json"), metadata);
      await writeFile(join(destination, "package-lock.json"), lock);
      await run(
        "npm",
        ["ci", "--prefix", destination, ...npm_install_options],
        checkout,
      );
    },
  );
  await link_node_modules(
    join(dependencies, "node_modules"),
    join(package_root, "node_modules"),
  );
}

async function backend_environment(
  checkout: string,
  cache: string,
): Promise<void> {
  const packages = ["@anthropic-ai/claude-code", "@openai/codex"];
  const versions = await Promise.all(
    packages.map(
      (name) =>
        new Promise<string>((complete, reject) => {
          const child = spawn(
            "npm",
            ["view", `${name}@latest`, "version", "--json"],
            { cwd: checkout, stdio: ["ignore", "pipe", "inherit"] },
          );
          let text = "";
          child.stdout.on("data", (data: Buffer) => {
            text += data.toString();
          });
          child.once("error", reject);
          child.once("exit", (code) => {
            if (code !== 0)
              return reject(new Error(`npm view exited: ${code}`));
            try {
              complete(JSON.parse(text) as string);
            } catch (error) {
              reject(error);
            }
          });
        }),
    ),
  );

  const exact = packages.map((name, index) => `${name}@${versions[index]}`);
  const key = dependency_key([
    "backends-v2",
    ...exact,
    process.versions.modules ?? "",
    process.platform,
    process.arch,
  ]);
  const dependencies = await cached_directory(
    cache,
    key,
    async (destination) => {
      await run(
        "npm",
        [
          "install",
          "--prefix",
          destination,
          "--package-lock=false",
          "--save=false",
          ...npm_install_options,
          ...exact,
        ],
        checkout,
      );
    },
  );
  await link_node_modules(
    join(dependencies, "node_modules"),
    join(checkout, "agent_backends/node_modules"),
  );
}

export async function setup(checkout = tree): Promise<void> {
  for (const path of [
    ".venv",
    "web/node_modules",
    "server/node_modules",
    "agent_backends/node_modules",
  ])
    if (await exists(join(checkout, path)))
      throw new Error(
        `Setup requires fresh dependencies: ${join(checkout, path)}`,
      );
  const cache = resolve(
    process.env[cache_environment] ||
      join(userInfo().homedir, ".cache/headroom/dependencies"),
  );
  await Promise.all([
    python_environment(checkout, cache),
    node_environment(checkout, cache, "web"),
    node_environment(checkout, cache, "server"),
    backend_environment(checkout, cache),
  ]);
}

if (
  process.argv[1] &&
  import.meta.url === pathToFileURL(resolve(process.argv[1])).href
) {
  try {
    if (process.argv[2] === "--detach")
      await detach_node_modules(resolve(process.argv[3]!));
    else await setup();
  } catch (error) {
    console.error(error);
    process.exitCode = 1;
  }
}
