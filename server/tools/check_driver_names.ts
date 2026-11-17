/** Driver names and driver packages stay inside server/drivers/. Each current site is an allowance. */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, posix, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import type { AnyAgentDriver } from '../drivers/core/driver.ts';
import { BUILT_IN_DRIVERS } from '../drivers/built_in.ts';
import { DRIVER_IMPORT_ALLOWANCES, DRIVER_NAME_ALLOWANCES } from './driver_name_allowances.ts';

/** The stages of the open driver model that remove allowances. */
export const DRIVER_STAGES = [
  'Closed lists',
  'Core',
  'Database',
  'Apps',
  'ACP',
  'Executor',
  /** Kept for good, each for its stated reason. */
  'Permanent',
] as const;
export type DriverStage = (typeof DRIVER_STAGES)[number];
export interface DriverNameAllowance {
  /** Repository-relative file, or a directory prefix ending in '/'. */
  readonly path: string;
  /** The driver whose names the path may contain. */
  readonly driver_kind: string;
  readonly reason: string;
  /** The stage that removes the allowance. */
  readonly until: DriverStage;
}
export interface DriverImportAllowance {
  /** Repository-relative file, or a directory prefix ending in '/'. */
  readonly path: string;
  /** The driver package the path may reach. */
  readonly driver_kind: string;
  readonly reason: string;
  readonly until: DriverStage;
}
export interface SourceFile {
  /** Repository-relative, with '/' separators. */
  readonly path: string;
  readonly text: string;
}
export type DriverFinding =
  | {
      readonly rule: 'name';
      readonly path: string;
      readonly line: number;
      readonly driver_kind: string;
      readonly text: string;
    }
  | {
      readonly rule: 'import';
      readonly path: string;
      readonly line: number;
      readonly target: string;
      /** The driver package reached from outside it, which an import allowance may cover. */
      readonly reached_driver_kind: string | null;
      readonly message: string;
    };
export interface DriverNameReport {
  readonly findings: readonly DriverFinding[];
  readonly uncovered: readonly DriverFinding[];
  readonly stale_names: readonly DriverNameAllowance[];
  readonly stale_imports: readonly DriverImportAllowance[];
  /** Allowances whose until names no stage. */
  readonly invalid: readonly (DriverNameAllowance | DriverImportAllowance)[];
}
type DriverFacts = Pick<AnyAgentDriver, 'driver_kind' | 'manifest'>;

const SCANNED_ROOTS = ['server/', 'web/src/', 'ios/Panels/'];
const EXEMPT_FILES = new Set([
  'server/drivers/built_in.ts',
  'server/tools/driver_name_allowances.ts',
]);
const PACKAGE_ENTRY_FILES = new Set([
  'driver.ts',
  'manifest.ts',
  'client.ts',
  'models.ts',
  'tool_detail.ts',
]);

function in_scope(path: string): boolean {
  if (!SCANNED_ROOTS.some((root) => path.startsWith(root)) || path.includes('/node_modules/'))
    return false;
  const source = path.startsWith('ios/')
    ? path.endsWith('.swift')
    : /\.(?:[cm]?tsx?|svelte)$/.test(path) && !/\.d\.[cm]?ts$/.test(path);
  return (
    source &&
    !(
      /\.test\.[cm]?tsx?$/.test(path) ||
      path.includes('/tests/') ||
      path.includes('/testing/') ||
      path === 'server/test_host.ts'
    )
  );
}
/**
 * Lower-case word parts: split on anything not alphanumeric, on camelCase and on letter/digit
 * boundaries. An accepted limit: words glued in all capitals (CLAUDECODE) stay one part.
 */
export function name_parts(text: string): string[] {
  return text
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/([A-Z]+)([A-Z][a-z])/g, '$1 $2')
    .replace(/(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])/g, ' ')
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean)
    .map((part) => part.toLowerCase());
}
function contains_sequence(parts: readonly string[], sequence: readonly string[]): boolean {
  for (let start = 0; start + sequence.length <= parts.length; start++)
    if (sequence.every((part, index) => parts[start + index] === part)) return true;
  return false;
}
/** Each driver's kind, executable override and home environment names. */
function banned_names(drivers: readonly DriverFacts[]): { kind: string; parts: string[] }[] {
  return drivers.flatMap((driver) =>
    [
      driver.driver_kind,
      driver.manifest.executable.override_environment_name,
      driver.manifest.home?.environment_name,
      ...(driver.manifest.home?.extra_environment_names ?? []),
    ]
      .filter((name): name is string => typeof name === 'string')
      .map((name) => ({ kind: driver.driver_kind, parts: name_parts(name) })),
  );
}
interface Token {
  readonly text: string;
  readonly line: number;
}
interface ImportReference {
  /** Null when the target is computed. */
  readonly specifier: string | null;
  /** The literal text inside a computed target, such as a template's fixed parts. */
  readonly computed_text: string;
  readonly line: number;
  readonly type_only: boolean;
  /** import() or a require call, rather than a static import. */
  readonly dynamic: boolean;
}
/**
 * require(), require.resolve(), and the same through createRequire(...). An ES module has no
 * require of its own, so a bare require call loads a module only where createRequire binds it.
 */
function is_require_call(node: ts.CallExpression, binds_require: boolean): boolean {
  const is_require = (callee: ts.Expression): boolean =>
    (binds_require && ts.isIdentifier(callee) && callee.text === 'require') ||
    (ts.isCallExpression(callee) &&
      ts.isIdentifier(callee.expression) &&
      callee.expression.text === 'createRequire');
  const callee = node.expression;
  return (
    is_require(callee) ||
    (ts.isPropertyAccessExpression(callee) &&
      callee.name.text === 'resolve' &&
      is_require(callee.expression))
  );
}
function is_module_specifier(node: ts.Node, binds_require: boolean): boolean {
  const parent = node.parent;
  if (!parent) return false;
  if (ts.isImportDeclaration(parent) || ts.isExportDeclaration(parent))
    return parent.moduleSpecifier === node;
  if (
    ts.isCallExpression(parent) &&
    (parent.expression.kind === ts.SyntaxKind.ImportKeyword ||
      is_require_call(parent, binds_require))
  )
    return parent.arguments[0] === node;
  if (ts.isExternalModuleReference(parent)) return parent.expression === node;
  return ts.isLiteralTypeNode(parent) && !!parent.parent && ts.isImportTypeNode(parent.parent);
}
function scan_typescript(text: string, line_offset = 0) {
  const source = ts.createSourceFile('source.ts', text, ts.ScriptTarget.Latest, true);
  const binds_require = /\bcreateRequire\b/.test(text);
  const literal_text = (node: ts.Node): string => {
    const parts: string[] = [];
    const collect = (child: ts.Node): void => {
      if (ts.isStringLiteralLike(child) || ts.isTemplateLiteralToken(child)) parts.push(child.text);
      ts.forEachChild(child, collect);
    };
    collect(node);
    return parts.join('');
  };
  const tokens: Token[] = [];
  const imports: ImportReference[] = [];
  const line_of = (node: ts.Node) =>
    source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1 + line_offset;
  const visit = (node: ts.Node): void => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier)) {
      // Only `import type` loads nothing. `import { type X }` still loads the module.
      imports.push({
        specifier: node.moduleSpecifier.text,
        line: line_of(node),
        type_only: node.importClause?.isTypeOnly ?? false,
        computed_text: '',
        dynamic: false,
      });
    } else if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference)
    ) {
      const expression = node.moduleReference.expression;
      imports.push({
        specifier: ts.isStringLiteralLike(expression) ? expression.text : null,
        computed_text: literal_text(expression),
        line: line_of(node),
        type_only: node.isTypeOnly,
        dynamic: false,
      });
    } else if (
      ts.isExportDeclaration(node) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      imports.push({
        specifier: node.moduleSpecifier.text,
        computed_text: '',
        line: line_of(node),
        type_only: node.isTypeOnly,
        dynamic: false,
      });
    } else if (
      ts.isCallExpression(node) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword || is_require_call(node, binds_require))
    ) {
      const argument = node.arguments[0];
      imports.push({
        specifier: argument && ts.isStringLiteralLike(argument) ? argument.text : null,
        computed_text: argument ? literal_text(argument) : '',
        line: line_of(node),
        type_only: false,
        dynamic: true,
      });
    } else if (
      ts.isImportTypeNode(node) &&
      ts.isLiteralTypeNode(node.argument) &&
      ts.isStringLiteral(node.argument.literal)
    ) {
      imports.push({
        specifier: node.argument.literal.text,
        computed_text: '',
        line: line_of(node),
        type_only: true,
        dynamic: false,
      });
    }
    if (ts.isIdentifier(node) || ts.isPrivateIdentifier(node))
      tokens.push({ text: node.text, line: line_of(node) });
    else if (
      (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) &&
      !(is_module_specifier(node, binds_require) && node.text.startsWith('.'))
    )
      tokens.push({ text: node.text, line: line_of(node) });
    else if (ts.isTemplateHead(node) || ts.isTemplateMiddle(node) || ts.isTemplateTail(node))
      tokens.push({ text: node.text, line: line_of(node) });
    else if (ts.isRegularExpressionLiteral(node))
      tokens.push({ text: node.text, line: line_of(node) });
    ts.forEachChild(node, visit);
  };
  visit(source);
  return { tokens, imports };
}
function line_at(text: string, index: number): number {
  let line = 1;
  for (let at = text.indexOf('\n'); at !== -1 && at < index; at = text.indexOf('\n', at + 1))
    line++;
  return line;
}
function word_tokens(text: string): Token[] {
  return [...text.matchAll(/[A-Za-z0-9_$]+/g)].map((match) => ({
    text: match[0],
    line: line_at(text, match.index),
  }));
}
/** Blank comments out, keeping line breaks and string contents. */
function without_c_comments(text: string): string {
  let output = '',
    quote: string | null = null;
  for (let index = 0; index < text.length; index++) {
    const character = text[index]!;
    if (quote !== null) {
      output += character;
      if (character === '\\') output += text[++index] ?? '';
      else if (character === quote || character === '\n') quote = null;
    } else if (character === '"') {
      quote = character;
      output += character;
    } else if (text.startsWith('//', index)) {
      while (index < text.length && text[index] !== '\n') index++;
      output += '\n';
    } else if (text.startsWith('/*', index)) {
      const end = text.indexOf('*/', index + 2);
      const stop = end === -1 ? text.length : end + 2;
      output += text.slice(index, stop).replace(/[^\n]/g, ' ');
      index = stop - 1;
    } else output += character;
  }
  return output;
}
function scan_svelte(text: string) {
  const tokens: Token[] = [];
  const imports: ImportReference[] = [];
  let markup = text.replace(/<!--[\s\S]*?-->/g, (comment) => comment.replace(/[^\n]/g, ' '));
  markup = markup.replace(
    /(<script\b[^>]*>)([\s\S]*?)(<\/script>)/g,
    (whole, open: string, body: string, close: string, index: number) => {
      const scanned = scan_typescript(body, line_at(text, index + open.length) - 1);
      tokens.push(...scanned.tokens);
      imports.push(...scanned.imports);
      return open + body.replace(/[^\n]/g, ' ') + close;
    },
  );
  tokens.push(...word_tokens(markup));
  return { tokens, imports };
}
function scan(file: SourceFile): { tokens: Token[]; imports: ImportReference[] } {
  if (file.path.endsWith('.swift'))
    return { tokens: word_tokens(without_c_comments(file.text)), imports: [] };
  if (file.path.endsWith('.svelte')) return scan_svelte(file.text);
  return scan_typescript(file.text);
}
function covered(path: string, prefix: string): boolean {
  return prefix.endsWith('/') ? path.startsWith(prefix) : path === prefix;
}
function import_findings(
  file: SourceFile,
  imports: readonly ImportReference[],
  kinds: ReadonlySet<string>,
): DriverFinding[] {
  const findings: DriverFinding[] = [];
  const add = (line: number, target: string, message: string, reached: string | null = null) =>
    findings.push({
      rule: 'import',
      path: file.path,
      line,
      target,
      reached_driver_kind: reached,
      message,
    });
  const package_of = (path: string): string | null => {
    const match = /^server\/drivers\/([^/]+)\//.exec(path);
    return match && kinds.has(match[1]!) ? match[1]! : null;
  };
  const own_package = package_of(file.path);
  const entry_file =
    own_package !== null &&
    dirname(file.path) === `server/drivers/${own_package}` &&
    PACKAGE_ENTRY_FILES.has(posix.basename(file.path));
  const driver_file = entry_file && posix.basename(file.path) === 'driver.ts';
  const package_root = `server/drivers/${own_package}/`;
  const entry_rule =
    'driver.ts, manifest.ts, client.ts, models.ts and tool_detail.ts import values only from drivers/core, core/records.ts, manifest.ts, client.ts, models.ts and tool_detail.ts.';
  for (const reference of imports) {
    if (reference.specifier === null) {
      // A computed path cannot be resolved. Refuse one in manifest.ts or client.ts, and one
      // anywhere else whose fixed text points into server/drivers. driver.ts may compute.
      if (!driver_file && (entry_file || /(?:^|\/)drivers\//.test(reference.computed_text)))
        add(reference.line, '(computed)', 'Only driver.ts may load a module from a computed path.');
      continue;
    }
    if (!reference.specifier.startsWith('.')) {
      if (file.path.startsWith('server/drivers/core/') && !reference.specifier.startsWith('node:'))
        add(reference.line, reference.specifier, 'drivers/core imports no package.');
      else if (entry_file && !reference.type_only && !reference.dynamic)
        add(reference.line, reference.specifier, entry_rule);
      continue;
    }
    const target = posix.normalize(posix.join(posix.dirname(file.path), reference.specifier));
    const target_package = package_of(target);
    if (target_package !== null && target_package !== own_package)
      add(
        reference.line,
        target,
        'Reach a driver package only through server/drivers/built_in.ts.',
        target_package,
      );
    if (!entry_file || reference.type_only) continue;
    if (reference.dynamic) {
      if (!driver_file)
        add(reference.line, target, 'Only driver.ts may load the runtime, with import().');
    } else if (
      !target.startsWith('server/drivers/core/') &&
      // The shared record guard, already in the installer bundle.
      target !== 'server/core/records.ts' &&
      target !== `${package_root}manifest.ts` &&
      target !== `${package_root}client.ts` &&
      target !== `${package_root}models.ts` &&
      target !== `${package_root}tool_detail.ts`
    )
      add(reference.line, target, entry_rule);
  }
  return findings;
}
export function check_driver_names(options: {
  readonly files: readonly SourceFile[];
  readonly drivers: readonly DriverFacts[];
  readonly name_allowances: readonly DriverNameAllowance[];
  readonly import_allowances: readonly DriverImportAllowance[];
}): DriverNameReport {
  const names = banned_names(options.drivers);
  const kinds = new Set(options.drivers.map((driver) => driver.driver_kind as string));
  const findings: DriverFinding[] = [];
  for (const file of options.files) {
    if (!in_scope(file.path) || EXEMPT_FILES.has(file.path)) continue;
    const { tokens, imports } = scan(file);
    findings.push(...import_findings(file, imports, kinds));
    const own = /^server\/drivers\/([^/]+)\//.exec(file.path)?.[1];
    const seen = new Set<string>();
    for (const token of tokens) {
      const parts = name_parts(token.text);
      for (const name of names) {
        if (name.kind === own || !contains_sequence(parts, name.parts)) continue;
        const key = `${token.line}\0${name.kind}\0${token.text}`;
        if (seen.has(key)) continue;
        seen.add(key);
        findings.push({
          rule: 'name',
          path: file.path,
          line: token.line,
          driver_kind: name.kind,
          text: token.text,
        });
      }
    }
  }
  const used_names = new Set<DriverNameAllowance>();
  const used_imports = new Set<DriverImportAllowance>();
  const uncovered = findings.filter((finding) => {
    if (finding.rule === 'name') {
      const allowance = options.name_allowances.find(
        (item) => item.driver_kind === finding.driver_kind && covered(finding.path, item.path),
      );
      if (allowance) used_names.add(allowance);
      return allowance === undefined;
    }
    const allowance = options.import_allowances.find(
      (item) =>
        item.driver_kind === finding.reached_driver_kind && covered(finding.path, item.path),
    );
    if (allowance) used_imports.add(allowance);
    return allowance === undefined;
  });
  return {
    findings,
    uncovered,
    stale_names: options.name_allowances.filter((item) => !used_names.has(item)),
    stale_imports: options.import_allowances.filter((item) => !used_imports.has(item)),
    invalid: [...options.name_allowances, ...options.import_allowances].filter(
      (item) => !(DRIVER_STAGES as readonly string[]).includes(item.until),
    ),
  };
}
function repository_files(root: string): SourceFile[] {
  const files: SourceFile[] = [];
  const walk = (directory: string): void => {
    for (const name of readdirSync(directory)) {
      if (name === 'node_modules' || name.startsWith('.')) continue;
      const path = join(directory, name);
      if (statSync(path).isDirectory()) walk(path);
      else {
        const relative_path = relative(root, path).split('\\').join('/');
        if (in_scope(relative_path))
          files.push({ path: relative_path, text: readFileSync(path, 'utf8') });
      }
    }
  };
  for (const scanned of SCANNED_ROOTS) {
    const directory = join(root, scanned);
    try {
      if (statSync(directory).isDirectory()) walk(directory);
    } catch {
      // A checkout without the iPhone or web tree has nothing there to check.
    }
  }
  return files.sort((left, right) => (left.path < right.path ? -1 : 1));
}
function main(print_findings: boolean): void {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
  const report = check_driver_names({
    files: repository_files(root),
    drivers: BUILT_IN_DRIVERS,
    name_allowances: DRIVER_NAME_ALLOWANCES,
    import_allowances: DRIVER_IMPORT_ALLOWANCES,
  });
  if (print_findings) {
    for (const finding of report.findings)
      console.log(
        finding.rule === 'name'
          ? `${finding.path}:${finding.line}: name ${finding.driver_kind}: ${finding.text}`
          : `${finding.path}:${finding.line}: import ${finding.target}: ${finding.message}`,
      );
    return;
  }
  for (const finding of report.uncovered)
    console.error(
      finding.rule === 'name'
        ? `${finding.path}:${finding.line}: names the ${finding.driver_kind} driver (${finding.text}). Move it into server/drivers/${finding.driver_kind}/ or ask the registry.`
        : `${finding.path}:${finding.line}: imports ${finding.target}. ${finding.message}`,
    );
  for (const allowance of report.stale_names)
    console.error(
      `Stale allowance: ${allowance.path} no longer names ${allowance.driver_kind}. Delete it (${allowance.until}).`,
    );
  for (const allowance of report.stale_imports)
    console.error(
      `Stale import allowance: ${allowance.path} no longer reaches the ${allowance.driver_kind} package. Delete it.`,
    );
  for (const allowance of report.invalid)
    console.error(
      `Allowance for ${allowance.path} names no stage: ${allowance.until}. Use one of ${DRIVER_STAGES.join(', ')}.`,
    );
  if (
    report.uncovered.length ||
    report.stale_names.length ||
    report.stale_imports.length ||
    report.invalid.length
  )
    process.exitCode = 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  main(process.argv.includes('--print-findings'));
