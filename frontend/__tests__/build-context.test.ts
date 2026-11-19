// @vitest-environment node
import path from 'node:path';
import ts from 'typescript';
import { expect, it } from 'vitest';

// The production image builds with frontend/ as its Docker context (#746), so
// the build's type check must stay inside it. CI also builds the real image
// (Frontend gates), which covers `.dockerignore` and anything this misses.
const root = path.resolve(__dirname, '..');

function parsedBuildConfig(): ts.ParsedCommandLine {
  const configPath = path.join(root, 'tsconfig.build.json');
  const { config, error } = ts.readConfigFile(configPath, ts.sys.readFile);
  expect(error).toBeUndefined();
  return ts.parseJsonConfigFileContent(
    config,
    ts.sys,
    root,
    undefined,
    configPath,
  );
}

const isTestFile = (f: string) =>
  /(^|\/)(__tests__|e2e)\//.test(f) || /\.(test|spec)\.[cm]?[jt]sx?$/.test(f);

it('excludes tests from the production build type check roots', () => {
  const roots = parsedBuildConfig().fileNames.map((f) =>
    path.relative(root, f),
  );
  expect(roots.length).toBeGreaterThan(0);
  expect(roots.filter(isTestFile)).toEqual([]);
});

// Resolve the whole program, as the build's type check does, so `@/` paths,
// type-only `import()` and transitive imports are followed too.
it('keeps the resolved build program inside the frontend build context', () => {
  const parsed = parsedBuildConfig();
  const program = ts.createProgram({
    rootNames: parsed.fileNames,
    options: parsed.options,
    projectReferences: parsed.projectReferences,
  });
  const files = program
    .getSourceFiles()
    .map((sf) => path.relative(root, sf.fileName))
    .filter((f) => !f.split(path.sep).includes('node_modules'));
  expect(files.filter((f) => f.startsWith('..'))).toEqual([]);
  // Playwright configs are build roots and may import e2e/ helpers, which
  // ship in the context; test suites themselves must stay out.
  expect(
    files.filter((f) => isTestFile(f) && !/^e2e\/[^/]+\.c?js$/.test(f)),
  ).toEqual([]);
}, 60_000);
