import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import {
  mkdtemp,
  mkdir,
  readFile,
  readdir,
  rm,
  writeFile,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import {
  cached_directory,
  node_environment,
  python_environment,
} from "./dev_setup.mts";

const python = (code: string, args: string[] = []): string =>
  execFileSync("python3", ["-I", "-c", code, ...args], {
    encoding: "utf8",
  }).trim();

// A real local wheel and editable backend avoid network and reuse the same Python host.
async function checkout(
  root: string,
  name: string,
  wheel: string,
): Promise<string> {
  const path = join(root, name);
  await mkdir(join(path, "src"), { recursive: true });
  await writeFile(
    join(path, "requirements.txt"),
    `tinydependency @ file://${wheel}\n`,
  );
  await writeFile(
    join(path, "pyproject.toml"),
    '[build-system]\nrequires=[]\nbuild-backend="fixture_backend"\nbackend-path=["."]\n',
  );
  await writeFile(
    join(path, "src/localproject.py"),
    `VALUE=${JSON.stringify(name)}\n`,
  );
  await writeFile(
    join(path, "fixture_backend.py"),
    `import pathlib,zipfile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    name='localproject-1.0-py3-none-any.whl'
    files={
      'localproject.pth':str(pathlib.Path(__file__).parent/'src')+'\\n',
      'localproject-1.0.dist-info/METADATA':'Metadata-Version: 2.1\\nName: localproject\\nVersion: 1.0\\n',
      'localproject-1.0.dist-info/WHEEL':'Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n',
      'localproject-1.0.dist-info/entry_points.txt':'[console_scripts]\\nlocalproject = tinydependency:main\\n',
      'localproject-1.0.dist-info/RECORD':''
    }
    with zipfile.ZipFile(str(pathlib.Path(wheel_directory)/name),'w') as archive:
        for path,text in files.items(): archive.writestr(path,text)
    return name
`,
  );
  return path;
}

test("two Python checkouts share built dependencies and keep source and launchers local", async () => {
  const root = await mkdtemp(join(tmpdir(), "panels-setup-python-"));
  try {
    const wheel = join(root, "tinydependency-1.0-py3-none-any.whl");
    python(
      `import zipfile,sys
files={
 'tinydependency.py':'def main():\\n    import localproject\\n    print(localproject.VALUE)\\n',
 'tinydependency-1.0.dist-info/METADATA':'Metadata-Version: 2.1\\nName: tinydependency\\nVersion: 1.0\\n',
 'tinydependency-1.0.dist-info/WHEEL':'Wheel-Version: 1.0\\nGenerator: fixture\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n',
 'tinydependency-1.0.dist-info/entry_points.txt':'[console_scripts]\\ntiny-check = tinydependency:main\\n',
 'tinydependency-1.0.dist-info/RECORD':''}
with zipfile.ZipFile(sys.argv[1],'w') as z:
 for path,text in files.items(): z.writestr(path,text)
`,
      [wheel],
    );
    const cache = join(root, "cache-" + "long-path-".repeat(16));
    const first = await checkout(root, "first", wheel);
    const second = await checkout(
      root,
      "second-" + "long-checkout-".repeat(12),
      wheel,
    );
    const old_target = process.env.PIP_TARGET;
    process.env.PIP_TARGET = join(root, "unwanted-target");
    try {
      await python_environment(first, cache);
      await python_environment(second, cache);
      assert.equal(
        (await readdir(cache)).filter((name) => !name.endsWith(".lock")).length,
        1,
      );
      for (const path of [first, second]) {
        assert.equal(
          execFileSync(join(path, ".venv/bin/tiny-check"), [], {
            encoding: "utf8",
          }).trim(),
          path.slice(root.length + 1),
        );
        assert.equal(
          execFileSync(join(path, ".venv/bin/localproject"), [], {
            encoding: "utf8",
          }).trim(),
          path.slice(root.length + 1),
        );
      }
    } finally {
      if (old_target === undefined) delete process.env.PIP_TARGET;
      else process.env.PIP_TARGET = old_target;
    }
    assert.ok(!(await readdir(root)).includes("unwanted-target"));
    // A source/config-only edit keeps dependencies reusable and updates imports immediately.
    await writeFile(join(second, "src/localproject.py"), 'VALUE="changed"\n');
    assert.equal(
      execFileSync(join(second, ".venv/bin/localproject"), [], {
        encoding: "utf8",
      }).trim(),
      "changed",
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("Node cache changes with dependency inputs and keeps mutation outside shared packages", async () => {
  const root = await mkdtemp(join(tmpdir(), "panels-setup-node-"));
  try {
    const cache = join(root, "cache");
    const first = join(root, "first");
    const second = join(root, "second");
    const dependency = join(root, "tiny-package");
    await mkdir(dependency);
    await writeFile(
      join(dependency, "package.json"),
      '{"name":"tiny-package","version":"1.0.0"}',
    );
    await writeFile(join(dependency, "index.js"), 'module.exports="sentinel";');
    execFileSync("npm", ["pack", "--pack-destination", root], {
      cwd: dependency,
      stdio: "pipe",
    });
    await mkdir(join(first, "web"), { recursive: true });
    await writeFile(
      join(first, "web/package.json"),
      JSON.stringify({
        name: "fixture",
        version: "1.0.0",
        devDependencies: {
          "tiny-package": `file:${join(root, "tiny-package-1.0.0.tgz")}`,
        },
      }),
    );
    execFileSync(
      "npm",
      ["install", "--package-lock-only", "--prefix", join(first, "web")],
      { stdio: "pipe" },
    );
    await mkdir(join(second, "web"), { recursive: true });
    for (const name of ["package.json", "package-lock.json"])
      await writeFile(
        join(second, "web", name),
        await readFile(join(first, "web", name)),
      );
    const old_omit = process.env.npm_config_omit;
    const old_mode = process.env.NODE_ENV;
    process.env.npm_config_omit = "dev";
    process.env.NODE_ENV = "production";
    try {
      for (const path of [first, second])
        await node_environment(path, cache, "web");
    } finally {
      if (old_omit === undefined) delete process.env.npm_config_omit;
      else process.env.npm_config_omit = old_omit;
      if (old_mode === undefined) delete process.env.NODE_ENV;
      else process.env.NODE_ENV = old_mode;
    }
    assert.equal(
      await readFile(
        join(first, "web/node_modules/tiny-package/index.js"),
        "utf8",
      ),
      'module.exports="sentinel";',
    );
    assert.equal(
      (await readdir(cache)).filter((name) => !name.endsWith(".lock")).length,
      1,
    );
    await mkdir(join(first, "web/node_modules/.vite"));
    await writeFile(join(first, "web/node_modules/.vite/own"), "private");
    assert.ok(
      !(await readdir(join(second, "web/node_modules"))).includes(".vite"),
    );
    const metadata = JSON.parse(
      await readFile(
        join(first, "web/node_modules/.panels-cache.json"),
        "utf8",
      ),
    ) as { source: string };
    const shared_lock = await readFile(
      join(metadata.source, ".package-lock.json"),
      "utf8",
    );
    await mkdir(join(first, "scripts"));
    for (const name of ["dev_launcher.py", "dev_setup.mts"])
      await writeFile(
        join(first, "scripts", name),
        await readFile(new URL("./" + name, import.meta.url)),
      );
    execFileSync(
      "python3",
      [
        "-I",
        "-S",
        "-B",
        join(first, "scripts/dev_launcher.py"),
        "npm",
        "--prefix",
        "web",
        "ci",
      ],
      { stdio: "pipe" },
    );
    assert.equal(
      await readFile(join(metadata.source, ".package-lock.json"), "utf8"),
      shared_lock,
    );
    await rm(join(second, "web/node_modules"), { recursive: true });
    const changed = JSON.parse(
      await readFile(join(second, "web/package.json"), "utf8"),
    );
    changed.version = "2.0.0";
    await writeFile(join(second, "web/package.json"), JSON.stringify(changed));
    execFileSync(
      "npm",
      ["install", "--package-lock-only", "--prefix", join(second, "web")],
      { stdio: "pipe" },
    );
    await node_environment(second, cache, "web");
    assert.equal(
      (await readdir(cache)).filter((name) => !name.endsWith(".lock")).length,
      2,
    );
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});

test("failed cache builds stay unavailable and concurrent callers build once", async () => {
  const root = await mkdtemp(join(tmpdir(), "panels-setup-lock-"));
  try {
    await assert.rejects(
      cached_directory(root, "fixture", async (path) => {
        await writeFile(join(path, "partial"), "unfinished");
        throw new Error("build failed");
      }),
      /build failed/,
    );
    let calls = 0;
    const build = async (path: string): Promise<void> => {
      calls++;
      assert.ok(!(await readdir(path)).includes("partial"));
      await writeFile(join(path, "complete"), "done");
    };
    const results = await Promise.all([
      cached_directory(root, "fixture", build),
      cached_directory(root, "fixture", build),
    ]);
    assert.equal(calls, 1);
    assert.equal(results[0], results[1]);
  } finally {
    await rm(root, { recursive: true, force: true });
  }
});
