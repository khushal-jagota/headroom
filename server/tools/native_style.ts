/** Enforce native-style rules on additions, without a permanent baseline allowlist. */
import { execFileSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';

interface Finding {
  key: string;
  line: number;
  message: string;
}
const flock_owner = 'server/core/locks/flock.ts';
const shutdown_owner = 'server/conversation/shutdown.ts';
const promise_owner = 'server/core/promise_observer.ts';

export function native_style_findings(path: string, text: string): Finding[] {
  const source = ts.createSourceFile(path, text, ts.ScriptTarget.Latest, true);
  const findings: Finding[] = [];
  const timer_names = new Set(['setTimeout']);
  const require_names = new Set(['require']);
  for (const statement of source.statements) {
    if (!ts.isImportDeclaration(statement) || !ts.isStringLiteral(statement.moduleSpecifier))
      continue;
    if (
      !['node:timers', 'node:timers/promises', 'timers', 'timers/promises'].includes(
        statement.moduleSpecifier.text,
      )
    )
      continue;
    const bindings = statement.importClause?.namedBindings;
    if (bindings && ts.isNamedImports(bindings))
      for (const binding of bindings.elements)
        if ((binding.propertyName ?? binding.name).text === 'setTimeout')
          timer_names.add(binding.name.text);
  }
  const add = (node: ts.Node, key: string, message: string): void => {
    findings.push({
      key,
      line: source.getLineAndCharacterOfPosition(node.getStart(source)).line + 1,
      message,
    });
  };
  const check_import = (node: ts.Node, module: string): void => {
    const bindings = ts.isImportDeclaration(node)
      ? node.importClause?.namedBindings
      : ts.isExportDeclaration(node)
        ? node.exportClause
        : undefined;
    const imports_repr =
      bindings &&
      (ts.isNamedImports(bindings) || ts.isNamedExports(bindings)) &&
      bindings.elements.some((binding) =>
        /^python_(?:string_)?repr$/.test((binding.propertyName ?? binding.name).text),
      );
    if (imports_repr || /(?:^|\/)python_repr(?:\.[cm]?[jt]s)?$/.test(module))
      add(
        node,
        'python-repr-import',
        'Use JavaScript values and plain Node errors instead of python_repr.',
      );
    if ((module === 'koffi' || module.startsWith('koffi/')) && path !== flock_owner)
      add(node, 'koffi-import', `Keep the native binding in ${flock_owner}.`);
  };
  const function_facts = (node: ts.Node): { timer: boolean; race: boolean } => {
    const facts = { timer: false, race: false };
    const inspect = (child: ts.Node): void => {
      if (ts.isCallExpression(child)) {
        const target = child.expression;
        if (ts.isIdentifier(target)) {
          facts.timer ||= timer_names.has(target.text);
        }
        if (ts.isPropertyAccessExpression(target)) {
          facts.timer ||= target.name.text === 'setTimeout';
          facts.race ||=
            ts.isIdentifier(target.expression) &&
            target.expression.text === 'Promise' &&
            target.name.text === 'race';
        }
      }
      ts.forEachChild(child, inspect);
    };
    inspect(node);
    return facts;
  };
  const visit = (node: ts.Node): void => {
    if (
      ts.isVariableDeclaration(node) &&
      ts.isIdentifier(node.name) &&
      node.initializer &&
      ts.isCallExpression(node.initializer) &&
      ts.isIdentifier(node.initializer.expression) &&
      node.initializer.expression.text === 'createRequire'
    )
      require_names.add(node.name.text);
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    )
      check_import(node, node.moduleSpecifier.text);
    if (
      ts.isImportEqualsDeclaration(node) &&
      ts.isExternalModuleReference(node.moduleReference) &&
      node.moduleReference.expression &&
      ts.isStringLiteral(node.moduleReference.expression)
    )
      check_import(node, node.moduleReference.expression.text);
    if (ts.isCallExpression(node) && node.arguments[0] && ts.isStringLiteral(node.arguments[0])) {
      const target = node.expression;
      if (
        target.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(target) && require_names.has(target.text)) ||
        (ts.isCallExpression(target) &&
          ts.isIdentifier(target.expression) &&
          target.expression.text === 'createRequire')
      )
        check_import(node, node.arguments[0].text);
      if (
        path !== flock_owner &&
        ts.isPropertyAccessExpression(target) &&
        target.name.text === 'func' &&
        /\bflock\b/.test(node.arguments[0].text)
      )
        add(node, 'flock-binding', `Use the shared binding in ${flock_owner}.`);
    }
    if (
      (ts.isFunctionDeclaration(node) ||
        ts.isMethodDeclaration(node) ||
        ts.isFunctionExpression(node) ||
        ts.isArrowFunction(node)) &&
      node.body
    ) {
      const name =
        ('name' in node ? node.name?.getText(source) : undefined) ??
        (ts.isVariableDeclaration(node.parent) ? node.parent.name.getText(source) : '<anonymous>');
      const facts = function_facts(node.body);
      if (path !== flock_owner && /flock/i.test(name))
        add(node, `flock-helper:${name}`, `Use the shared flock helper in ${flock_owner}.`);
      const canonical_observer =
        (path === shutdown_owner && name === 'observe_shutdown') ||
        (path === promise_owner && name === 'observe_promise');
      if (
        !canonical_observer &&
        ((facts.timer && facts.race) ||
          /^(?:bounded|within|deadline|with_deadline|with_timeout)$/.test(name))
      )
        add(
          node,
          `deadline-helper:${name}`,
          'Use AbortSignal.timeout/any instead of another deadline helper.',
        );
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return findings;
}

export function new_native_style_findings(path: string, before: string, after: string): Finding[] {
  const allowance = new Map<string, number>();
  for (const finding of native_style_findings(path, before))
    allowance.set(finding.key, (allowance.get(finding.key) ?? 0) + 1);
  return native_style_findings(path, after).filter((finding) => {
    const left = allowance.get(finding.key) ?? 0;
    if (!left) return true;
    allowance.set(finding.key, left - 1);
    return false;
  });
}

function main(base: string): void {
  const root = execFileSync('git', ['rev-parse', '--show-toplevel'], { encoding: 'utf8' }).trim();
  const git = (args: string[]): string =>
    execFileSync('git', args, { cwd: root, encoding: 'utf8' });
  const paths = git(['diff', '--name-only', '-z', '--diff-filter=ACMR', base, '--', 'server'])
    .split('\0')
    .filter((path) => /\.[cm]?tsx?$/.test(path));
  let failures = 0;
  for (const path of paths) {
    let before = '';
    // New files have no base blob. Other Git failures remain fatal.
    if (git(['ls-tree', base, '--', path]).trim()) before = git(['show', `${base}:${path}`]);
    for (const finding of new_native_style_findings(
      path,
      before,
      readFileSync(resolve(root, path), 'utf8'),
    )) {
      console.error(`${path}:${finding.line}: ${finding.message}`);
      failures++;
    }
  }
  if (failures) process.exitCode = 1;
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (!process.argv[2]) throw new Error('Pass the pull request base commit.');
  main(process.argv[2]);
}
