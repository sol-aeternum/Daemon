'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { createRequire } = require('node:module');
const braces = require(path.resolve(process.argv[2]));
const modules = path.resolve(process.argv[3]);
const checks = [];
let baselineRows = 0;
function check(name, fn) {
  fn();
  checks.push(name);
}
const nested = (n, left = '{', right = '}') =>
  left.repeat(n) + 'x' + right.repeat(n);
function refusal(fn, type) {
  assert.throws(
    fn,
    (error) => error instanceof type && /exceeds max depth/.test(error.message),
  );
}
function ast(n, type = 'brace', rooted = true) {
  let node = { type: 'text', value: 'x' };
  for (let i = 0; i < n; i++) {
    const parent = {
      type,
      commas: 1,
      ranges: 0,
      open: true,
      close: true,
      nodes: [node],
    };
    node.parent = parent;
    node = parent;
  }
  if (!rooted) return node;
  const root = { type: 'root', nodes: [node] };
  node.parent = root;
  return root;
}

async function main() {
  for (const op of ['parse', 'compile', 'expand', 'stringify']) {
    check(`${op} string boundary`, () => {
      assert.doesNotThrow(() => braces[op](nested(100)));
      refusal(() => braces[op](nested(101)), SyntaxError);
      refusal(() => braces[op](nested(4500)), SyntaxError);
    });
    check(`${op} parentheses`, () =>
      refusal(() => braces[op](nested(101, '(', ')')), SyntaxError),
    );
    check(`${op} mixed`, () =>
      refusal(
        () => braces[op]('({'.repeat(50) + '(x)' + '})'.repeat(50)),
        SyntaxError,
      ),
    );
    check(`${op} stricter`, () =>
      refusal(() => braces[op](nested(2), { maxDepth: 1 }), SyntaxError),
    );
    check(`${op} stricter boundary`, () =>
      assert.doesNotThrow(() => braces[op](nested(2), { maxDepth: 2 })),
    );
    check(`${op} option cap`, () =>
      refusal(() => braces[op](nested(101), { maxDepth: 100000 }), SyntaxError),
    );
    check(`${op} zero/negative`, () => {
      for (const maxDepth of [0, -1])
        refusal(() => braces[op]('{x}', { maxDepth }), SyntaxError);
    });
    check(`${op} invalid/nonfinite`, () => {
      for (const maxDepth of [
        NaN,
        Infinity,
        -Infinity,
        undefined,
        null,
        '10000',
        {},
        true,
      ]) {
        assert.doesNotThrow(() => braces[op](nested(100), { maxDepth }));
        refusal(() => braces[op](nested(101), { maxDepth }), SyntaxError);
      }
    });
  }
  check('default API', () => refusal(() => braces(nested(101)), SyntaxError));
  for (const op of ['compile', 'expand', 'stringify']) {
    for (const type of ['brace', 'paren'])
      for (const rooted of [true, false]) {
        check(`${op} ${type} AST ${rooted}`, () => {
          assert.doesNotThrow(() => braces[op](ast(100, type, rooted)));
          refusal(() => braces[op](ast(101, type, rooted)), RangeError);
          refusal(() => braces[op](ast(4500, type, rooted)), RangeError);
        });
      }
    check(`${op} AST options`, () => {
      refusal(() => braces[op](ast(2), { maxDepth: 1 }), RangeError);
      for (const maxDepth of [
        100000,
        NaN,
        Infinity,
        -Infinity,
        null,
        '10000',
      ]) {
        refusal(() => braces[op](ast(101), { maxDepth }), RangeError);
      }
      for (const maxDepth of [0, -1])
        refusal(() => braces[op](ast(1), { maxDepth }), RangeError);
    });
    check(`${op} child cycle`, () => {
      const root = { type: 'root', nodes: [] };
      root.nodes.push(root);
      refusal(() => braces[op](root), RangeError);
    });
  }
  check('normal compile', () =>
    assert.deepEqual(braces('src/{app,components}/*.tsx'), [
      'src/(app|components)/*.tsx',
    ]),
  );
  check('normal expand', () =>
    assert.deepEqual(braces.expand('src/{app,components}/*.tsx'), [
      'src/app/*.tsx',
      'src/components/*.tsx',
    ]),
  );
  check('normal range', () =>
    assert.deepEqual(braces.expand('{1..3}'), ['1', '2', '3']),
  );
  check('flat range limit', () =>
    assert.throws(() => braces.expand('{1..50000}'), /range limit/),
  );
  check('flat range compile', () =>
    assert.doesNotThrow(() => braces.compile('{1..50000}')),
  );
  check('escapes/quotes', () => {
    assert.doesNotThrow(() =>
      braces.expand('\\{'.repeat(101) + 'x' + '\\}'.repeat(101)),
    );
    for (const quote of ['"', "'", '`'])
      assert.doesNotThrow(() => braces.compile(quote + nested(101) + quote));
  });
  check('ordinary options', () => {
    assert.deepEqual(
      braces.expand('{a,a,,}', { nodupes: true, noempty: true }),
      ['a'],
    );
    assert.throws(
      () => braces.parse('123456', { maxLength: 3 }),
      /max characters/,
    );
    assert.throws(() => braces.parse({}), TypeError);
  });
  check('imbalance', () => {
    refusal(() => braces.parse('{'.repeat(101)), SyntaxError);
    refusal(() => braces.parse('('.repeat(101)), SyntaxError);
    refusal(() => braces.parse(')}'.repeat(50) + nested(101)), SyntaxError);
  });
  check('unmatched closer falsifiers', () => {
    for (const n of [1, 150, 1000])
      for (const op of ['parse', 'compile', 'expand']) {
        refusal(() => braces[op]('}'.repeat(n) + '('.repeat(101)), SyntaxError);
      }
  });
  check('fractional option semantics', () => {
    assert.doesNotThrow(() => braces.parse('{x}', { maxDepth: 0.5 }));
    refusal(() => braces.compile('{x}', { maxDepth: 0.5 }), RangeError);
  });
  check('216 baseline differential rows', () => {
    const rows = JSON.parse(
      fs.readFileSync(
        path.resolve(__dirname, '../vendor/braces-baseline-golden.json'),
        'utf8',
      ),
    );
    assert.equal(
      rows.length,
      216,
      'Expected full original compatibility corpus',
    );
    baselineRows = rows.length;
    for (const row of rows) {
      if (row.error)
        assert.throws(
          () => braces[row.op](row.input, row.options),
          (e) => e.name === row.error && e.message === row.message,
        );
      else assert.deepEqual(braces[row.op](row.input, row.options), row.result);
    }
  });

  // Real installed consumers: no global module-resolution substitution here.
  const micromatch = require(path.join(modules, 'micromatch'));
  check('micromatch expansion', () =>
    assert.deepEqual(micromatch.braceExpand('src/{app,components}/*.tsx'), [
      'src/app/*.tsx',
      'src/components/*.tsx',
    ]),
  );
  check('micromatch matching', () =>
    assert.deepEqual(micromatch(['a.ts', 'b.js', 'c.py'], '*.{ts,js}'), [
      'a.ts',
      'b.js',
    ]),
  );
  check('micromatch oversized', () =>
    refusal(() => micromatch.braceExpand(nested(101)), SyntaxError),
  );
  const fixture = fs.mkdtempSync(path.join(os.tmpdir(), 'daemon-braces-test-'));
  const watchers = [];
  try {
    for (const file of [
      'src/app/one.tsx',
      'src/components/two.tsx',
      'src/other/no.md',
    ]) {
      const target = path.join(fixture, file);
      fs.mkdirSync(path.dirname(target), { recursive: true });
      fs.writeFileSync(target, 'fictional fixture\n');
    }
    const globPackages = [
      path.join(modules, 'fast-glob'),
      createRequire(path.join(modules, 'tailwindcss/package.json')).resolve(
        'fast-glob',
      ),
    ];
    for (const entry of globPackages) {
      const glob = require(entry);
      check(`${entry} ordinary glob`, () =>
        assert.deepEqual(
          glob.sync('src/{app,components}/*.tsx', { cwd: fixture }).sort(),
          ['src/app/one.tsx', 'src/components/two.tsx'],
        ),
      );
      check(`${entry} oversized glob`, () =>
        refusal(() => glob.sync(nested(101), { cwd: fixture }), SyntaxError),
      );
    }
    const chokidar = require(path.join(modules, 'chokidar'));
    const watcher = chokidar.watch('src/{app,components}/*.tsx', {
      cwd: fixture,
      persistent: false,
    });
    watchers.push(watcher);
    const files = [];
    watcher.on('add', (file) => files.push(file));
    await new Promise((resolve, reject) => {
      watcher.once('ready', resolve);
      watcher.once('error', reject);
    });
    check('chokidar ordinary watch', () =>
      assert.deepEqual(files.sort(), [
        'src/app/one.tsx',
        'src/components/two.tsx',
      ]),
    );
    const empty = chokidar.watch([], { cwd: fixture, persistent: false });
    watchers.push(empty);
    check('chokidar oversized helper', () =>
      refusal(() => empty._getWatchHelpers(nested(101), 0), SyntaxError),
    );
  } finally {
    for (const watcher of watchers) await watcher.close();
    // Only the uniquely created, owned test directory is retired.
    fs.rmSync(fixture, { recursive: true, force: true });
  }
  console.log(
    JSON.stringify({
      node: process.version,
      checks: checks.length,
      baselineRows,
      installedCode: process.argv[2],
    }),
  );
}
main().catch((error) => {
  console.error(error.stack);
  process.exitCode = 1;
});
