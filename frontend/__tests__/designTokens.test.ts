// @vitest-environment node
import { describe, expect, it } from 'vitest';
import { Linter } from 'eslint';
import designTokens from '../eslint-rules/design-tokens.mjs';

function lint(code: string) {
  return new Linter().verify(code, {
    languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
    plugins: { tokens: designTokens },
    rules: { 'tokens/no-literal-values': 'error' },
  });
}

describe('design-token lint policy', () => {
  it('rejects literal colors, sizes, and shadows with responsive/state variants', () => {
    const messages = lint(
      '<div className="text-white hover:bg-black/50 md:text-[11px] min-h-[44px] shadow-[0_0_12px_red]" />',
    );
    expect(messages).toHaveLength(5);
    expect(
      messages.every(({ ruleId }) => ruleId === 'tokens/no-literal-values'),
    ).toBe(true);
  });

  it('checks conditional templates and shared class maps', () => {
    expect(
      lint(
        'const styles = { active: "bg-white" }; const el = <div className={`text-[10px] ${active ? "bg-black" : "bg-media"}`} />;',
      ),
    ).toHaveLength(3);
  });

  it('rejects legacy namespaces and arbitrary properties', () => {
    expect(
      lint(
        '<div className="text-[var(--daemon-text-primary)] bg-daemon-accent [padding:13px]" />',
      ),
    ).toHaveLength(3);
  });

  it('permits named utilities, CSS variables, alpha modifiers, and selector variants', () => {
    expect(
      lint(
        '<div className="min-h-touch text-xs text-[var(--color-text-primary)] bg-media/50 md:w-sidebar [&_p]:text-[color:var(--color-text-primary)]" />',
      ),
    ).toEqual([]);
  });

  it('does not mistake array access, prose, or a URL for a utility', () => {
    expect(
      lint(
        'const name = items[0]; const message = "A black and white photograph"; const url = "https://example.com/image[1].png"; const header = "x-daemon-rate-limit-scope";',
      ),
    ).toEqual([]);
  });

  it('rejects interpolation inside an arbitrary class and important literals', () => {
    expect(
      lint(
        'const el = <div className={`h-[${height}px] !text-white hover:!bg-black/50`} />;',
      ),
    ).toHaveLength(3);
  });
});
