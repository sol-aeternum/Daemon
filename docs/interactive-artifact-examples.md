# HTML Preview Examples

This document shows static HTML previews rendered inline in chat with the `html:interactive` code block tag. The tag name is legacy: despite the word "interactive", the preview is a closed, sanitized fragment of factual visible markup. It has no JavaScript, authored styles, controls, or post-render behaviour.

For ordinary answers, use Markdown. Reserve this preview for a static snapshot where layout or a simple inline SVG genuinely helps.

## Usage

Put the markup in a Markdown code block tagged `html:interactive`:

````markdown
```html:interactive
<h2>Title</h2>
<p>Baked-in content.</p>
```
````

Rules of thumb:

- Output a body fragment only, without `<!DOCTYPE>`, `<html>`, `<head>`, or `<body>`.
- Use semantic headings, paragraphs, lists, and tables.
- For simple inline SVG, put geometry in a `path`'s `d` attribute or use `circle` with `cx`, `cy`, and `r`. The sanitizer strips `x`, `y`, and `transform`, so do not rely on positioned `rect` or `text`. Put labels in the surrounding HTML.
- Bake all values, labels, totals, and units into the markup. Nothing is fetched or computed after render.
- Do not include JavaScript, event handlers, `<script>`, `<style>` blocks, `style` attributes, forms, inputs, selects, buttons, external styles, fonts, images, or network requests.
- Do not include resize messaging. The client owns presentation and sizing.
- Do not claim the preview is interactive, dynamic, saved, or durable. It is a static render and not a durable artifact.
- Keep the markup compact and the surrounding prose short.

## Example 1: Static Savings Snapshot

A fixed set of compounded balances, presented as a table. No calculator, sliders, or recomputation.

````markdown
```html:interactive
<h2>Savings growth snapshot</h2>
<p>A fixed deposit of <strong>$1,000</strong> at <strong>5%</strong> annual interest, compounded yearly.</p>
<table>
  <thead>
    <tr><th>Year</th><th>Balance</th></tr>
  </thead>
  <tbody>
    <tr><td>1</td><td>$1,050.00</td></tr>
    <tr><td>2</td><td>$1,102.50</td></tr>
    <tr><td>3</td><td>$1,157.63</td></tr>
    <tr><td>4</td><td>$1,215.51</td></tr>
    <tr><td>5</td><td>$1,276.28</td></tr>
    <tr><td>6</td><td>$1,340.10</td></tr>
    <tr><td>7</td><td>$1,407.10</td></tr>
    <tr><td>8</td><td>$1,477.46</td></tr>
    <tr><td>9</td><td>$1,551.33</td></tr>
    <tr><td>10</td><td>$1,628.89</td></tr>
  </tbody>
</table>
<p>Total growth over 10 years: <strong>$628.89</strong>.</p>
```
````

## Example 2: Static Trend Shape

An inline SVG trend line with all points fixed in the path data, plus a table for the exact values. The chart does not update or respond to input.

````markdown
```html:interactive
<h2>Monthly usage</h2>
<p>Recorded values for the first six months.</p>
<svg viewBox="0 0 480 220" width="480" height="220" fill="none">
  <path d="M40 40 H440 M40 80 H440 M40 120 H440 M40 160 H440 M40 200 H440" stroke="#666" stroke-width="1" fill="none"></path>
  <path d="M40 136 L120 104 L200 120 L280 72 L360 83 L440 40" stroke="#4f8cff" stroke-width="3" fill="none"></path>
  <circle cx="40" cy="136" r="4" fill="#4f8cff"></circle>
  <circle cx="120" cy="104" r="4" fill="#4f8cff"></circle>
  <circle cx="200" cy="120" r="4" fill="#4f8cff"></circle>
  <circle cx="280" cy="72" r="4" fill="#4f8cff"></circle>
  <circle cx="360" cy="83" r="4" fill="#4f8cff"></circle>
  <circle cx="440" cy="40" r="4" fill="#4f8cff"></circle>
</svg>
<table>
  <thead>
    <tr><th>Month</th><th>Value</th></tr>
  </thead>
  <tbody>
    <tr><td>January</td><td>12</td></tr>
    <tr><td>February</td><td>18</td></tr>
    <tr><td>March</td><td>15</td></tr>
    <tr><td>April</td><td>24</td></tr>
    <tr><td>May</td><td>22</td></tr>
    <tr><td>June</td><td>30</td></tr>
  </tbody>
</table>
```
````

## Example 3: Static Answer Review

A completed review with the outcome already decided. This is not a quiz widget: there are no options to click and no feedback logic.

````markdown
```html:interactive
<h2>Review: capital of France</h2>
<p><strong>Question:</strong> What is the capital of France?</p>
<table>
  <thead>
    <tr><th>Option</th><th>Result</th></tr>
  </thead>
  <tbody>
    <tr><td>London</td><td>Incorrect</td></tr>
    <tr><td>Paris</td><td><strong>Correct</strong></td></tr>
    <tr><td>Berlin</td><td>Incorrect</td></tr>
    <tr><td>Madrid</td><td>Incorrect</td></tr>
  </tbody>
</table>
<p>Paris is the capital of France.</p>
```
````

## Requesting Executable HTML Source

If the user explicitly asks for executable HTML, use an ordinary `html` fence and say clearly that it is source only and will not run in the chat preview. The preview tag is not a sandbox for applications.

````markdown
```html
<!DOCTYPE html>
<html>
<body>
  <button id="demo">Click me</button>
  <script>
    document.getElementById('demo').addEventListener('click', function () {
      this.textContent = 'Clicked';
    });
  </script>
</body>
</html>
```
````

Do not mix the two forms: `html:interactive` is for the static preview, and `html` is for source the user will run themselves.

## Presentation

The client supplies the surrounding shell styling for text, tables, and simple SVG. Do not author CSS, theme variables, or inline styles; they are removed. Do not try to size the container or post messages to the parent page.

## Best Practices

1. **Markdown first**: use this preview only when static layout or inline SVG adds real value.
2. **Bake it in**: every number, label, and unit belongs in the markup.
3. **Stay semantic**: headings, paragraphs, lists, tables, and simple inline SVG.
4. **No behaviour**: no scripts, styles, controls, forms, external resources, or resize messaging.
5. **Describe only what renders**: do not claim interactivity, saved state, or durability.
