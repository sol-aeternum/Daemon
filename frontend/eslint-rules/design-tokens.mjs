// Examine string literals as well as JSX attributes so conditional classes,
// variant maps, and template literals have the same token policy.
function utilityOf(className) {
  let depth = 0;
  let start = 0;
  for (let index = 0; index < className.length; index += 1) {
    if (className[index] === '[') depth += 1;
    if (className[index] === ']') depth -= 1;
    if (className[index] === ':' && depth === 0) start = index + 1;
  }
  return className.slice(start).replace(/^!/, '').replace(/!$/, '');
}

/** @type {import('eslint').Rule.RuleModule} */
const rule = {
  meta: {
    type: 'suggestion',
    docs: {
      description: 'Use design tokens instead of literal Tailwind values.',
    },
    schema: [],
    messages: {
      token: 'Use a design token or scale utility instead of "{{className}}".',
    },
  },
  create(context) {
    function check(node, value) {
      if (typeof value !== 'string') return;
      for (const className of value.split(/\s+/)) {
        const utility = utilityOf(className);
        const arbitrary = utility.match(/^-?[a-z][\w-]*-\[(.*)\](?:\/\d+)?$/);
        const usesToken =
          arbitrary && /^(?:[a-z-]+:)?var\(--[\w-]+\)$/.test(arbitrary[1]);
        const hasLiteralValue =
          (utility.includes('-[') && !usesToken) || /^\[[\w-]+:/.test(utility);
        const hasLiteralColor =
          /^(?:text|bg|border(?:-[trblxy])?|from|via|to|fill|stroke|ring(?:-offset)?|shadow|divide)-(?:white|black)(?:\/\d+)?$/.test(
            utility,
          );
        const usesLegacyToken =
          utility.includes('--daemon-') ||
          /^(?:text|bg|border|shadow|ring)-daemon-/.test(utility);
        if (hasLiteralValue || hasLiteralColor || usesLegacyToken) {
          context.report({ node, messageId: 'token', data: { className } });
        }
      }
    }
    return {
      Literal: (node) => check(node, node.value),
      TemplateElement: (node) => check(node, node.value.cooked),
    };
  },
};

const designTokens = { rules: { 'no-literal-values': rule } };
export default designTokens;
