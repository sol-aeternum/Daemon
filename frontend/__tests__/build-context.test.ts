// @vitest-environment node
import { readFileSync } from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

// The production image builds with frontend/ as its Docker context (#746), so
// every file the build type-checks must resolve its relative imports inside it.
const root = path.resolve(__dirname, '..');

function buildScope(): string[] {
  const configPath = path.join(root, 'tsconfig.build.json');
  const { config, error } = ts.readConfigFile(configPath, ts.sys.readFile);
  expect(error).toBeUndefined();
  return ts.parseJsonConfigFileContent(
    config,
    ts.sys,
    root,
    undefined,
    configPath,
  ).fileNames;
}

function relativeSpecifiers(file: string): string[] {
  const source = ts.createSourceFile(
    file,
    readFileSync(file, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
  );
  const found: string[] = [];
  const visit = (node: ts.Node) => {
    if (
      (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) &&
      node.moduleSpecifier &&
      ts.isStringLiteral(node.moduleSpecifier)
    ) {
      found.push(node.moduleSpecifier.text);
    } else if (
      ts.isCallExpression(node) &&
      node.arguments.length > 0 &&
      ts.isStringLiteral(node.arguments[0]) &&
      (node.expression.kind === ts.SyntaxKind.ImportKeyword ||
        (ts.isIdentifier(node.expression) &&
          node.expression.text === 'require'))
    ) {
      found.push(node.arguments[0].text);
    }
    ts.forEachChild(node, visit);
  };
  visit(source);
  return found.filter((s) => s.startsWith('.'));
}

it('excludes tests from the production build type check', () => {
  const scope = buildScope().map((f) => path.relative(root, f));
  expect(scope.length).toBeGreaterThan(0);
  expect(scope.filter((f) => /(^|\/)(__tests__|e2e)\//.test(f))).toEqual([]);
  expect(scope.filter((f) => /\.(test|spec)\.tsx?$/.test(f))).toEqual([]);
});

it('keeps every build-checked import inside the frontend build context', () => {
  const escaping = buildScope().flatMap((file) =>
    relativeSpecifiers(file)
      .filter((spec) => {
        const target = path.resolve(path.dirname(file), spec);
        return path.relative(root, target).startsWith('..');
      })
      .map((spec) => `${path.relative(root, file)} -> ${spec}`),
  );
  expect(escaping).toEqual([]);
});
