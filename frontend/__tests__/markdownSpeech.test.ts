import { expect, it } from 'vitest';
import { markdownToSpeechText } from '../lib/markdownSpeech';

it('reads prose formatting as content, not asterisks, hashes or backticks', () => {
  expect(
    markdownToSpeechText(
      '# Hello\n\n**Strong** and *gentle* with `inline_code`.\n\n- One\n- Two',
    ),
  ).toBe('Hello\nStrong and gentle with inline_code.\nOne\nTwo');
});

it('preserves fenced code content and literal punctuation/math', () => {
  expect(
    markdownToSpeechText(
      'Use `a * b` or x * y.\n\n```python\nresult = a * b\nname_with_underscores = "**literal**"\n```',
    ),
  ).toBe(
    'Use a * b or x * y.\nresult = a * b\nname_with_underscores = "**literal**"',
  );
  expect(markdownToSpeechText('Literal \\*asterisk\\* and snake_case.')).toBe(
    'Literal *asterisk* and snake_case.',
  );
});

it('reads link labels, reference labels and image alt text without destinations', () => {
  expect(
    markdownToSpeechText(
      '[Guide](https://example.test) ![Diagram](image.png) [Reference][ref]\n\n[ref]: https://example.test/reference',
    ),
  ).toBe('Guide Diagram Reference');
});

it('separates paragraphs, list items and table cells without speaking syntax', () => {
  expect(
    markdownToSpeechText(
      '> First\n>\n> Second\n\n| A | B |\n|---|---|\n| 1 | 2 |',
    ),
  ).toBe('First\nSecond\nA, B\n1, 2');
});

it('keeps Unicode, malformed/literal markers, and code whitespace intact', () => {
  expect(markdownToSpeechText('Unclosed **bold. 中文 😀')).toBe(
    'Unclosed **bold. 中文 😀',
  );
  expect(markdownToSpeechText('```\n  indented * value\n\nnext\n```')).toBe(
    '  indented * value\n\nnext',
  );
});

it('preserves raw HTML as literal content without interpreting it', () => {
  expect(markdownToSpeechText('Before <em>word</em> after.')).toBe(
    'Before <em>word</em> after.',
  );
  expect(markdownToSpeechText('<div>Literal block content</div>')).toBe(
    '<div>Literal block content</div>',
  );
  expect(markdownToSpeechText('<script>alert("never")</script>')).toBe(
    '<script>alert("never")</script>',
  );
});
