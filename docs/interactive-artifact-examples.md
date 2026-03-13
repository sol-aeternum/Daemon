# Interactive HTML Artifact Examples

This document provides working examples of interactive HTML artifacts that can be rendered inline in chat messages using the `html:interactive` code block syntax.

## Usage

To use an artifact, copy the HTML code into a markdown code block with the `html:interactive` language tag:

````markdown
```html:interactive
<!-- Paste artifact HTML here -->
```
````

## Example 1: Compound Interest Calculator

A calculator that shows how compound interest grows over time with adjustable sliders.

````markdown
```html:interactive
<!DOCTYPE html>
<html>
<head>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-secondary);
      color: var(--text-primary);
      font-family: system-ui, -apple-system, sans-serif;
      padding: 1.5rem;
      min-height: 100vh;
    }
    .calculator {
      background: var(--bg-primary);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      max-width: 400px;
    }
    h2 {
      font-size: 1.25rem;
      margin-bottom: 1rem;
      color: var(--text-primary);
    }
    .input-group {
      margin-bottom: 1rem;
    }
    label {
      display: block;
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-bottom: 0.5rem;
    }
    input[type="range"] {
      width: 100%;
      accent-color: var(--accent);
    }
    .value-display {
      font-size: 1.5rem;
      font-weight: 600;
      color: var(--accent);
      margin-top: 0.25rem;
    }
    .result {
      margin-top: 1.5rem;
      padding: 1rem;
      background: var(--bg-tertiary);
      border-radius: 0.5rem;
    }
    .result-label {
      font-size: 0.875rem;
      color: var(--text-secondary);
    }
    .result-value {
      font-size: 2rem;
      font-weight: 700;
      color: var(--status-success);
      margin-top: 0.25rem;
    }
  </style>
</head>
<body>
  <div class="calculator">
    <h2>Compound Interest Calculator</h2>
    
    <div class="input-group">
      <label>Principal Amount ($)</label>
      <input type="range" id="principal" min="100" max="10000" value="1000" step="100">
      <div class="value-display" id="principalValue">$1,000</div>
    </div>
    
    <div class="input-group">
      <label>Annual Interest Rate (%)</label>
      <input type="range" id="rate" min="1" max="20" value="5" step="0.5">
      <div class="value-display" id="rateValue">5%</div>
    </div>
    
    <div class="input-group">
      <label>Time Period (Years)</label>
      <input type="range" id="years" min="1" max="30" value="10" step="1">
      <div class="value-display" id="yearsValue">10 years</div>
    </div>
    
    <div class="result">
      <div class="result-label">Future Value</div>
      <div class="result-value" id="result">$1,629.47</div>
    </div>
  </div>
  
  <script>
    const principalInput = document.getElementById('principal');
    const rateInput = document.getElementById('rate');
    const yearsInput = document.getElementById('years');
    
    function calculate() {
      const principal = parseFloat(principalInput.value);
      const rate = parseFloat(rateInput.value) / 100;
      const years = parseInt(yearsInput.value);
      
      const futureValue = principal * Math.pow(1 + rate, years);
      
      document.getElementById('principalValue').textContent = '$' + principal.toLocaleString();
      document.getElementById('rateValue').textContent = rateInput.value + '%';
      document.getElementById('yearsValue').textContent = years + ' years';
      document.getElementById('result').textContent = '$' + futureValue.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    }
    
    principalInput.addEventListener('input', calculate);
    rateInput.addEventListener('input', calculate);
    yearsInput.addEventListener('input', calculate);
    
    calculate();
    
    function sendHeight() {
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'artifact-height', height: document.body.scrollHeight }, '*');
      }
    }
    window.addEventListener('load', sendHeight);
    window.addEventListener('resize', sendHeight);
  </script>
</body>
</html>
```
````

## Example 2: Interactive Comparison Bar Chart

A simple bar chart that compares values with hover tooltips.

````markdown
```html:interactive
<!DOCTYPE html>
<html>
<head>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-secondary);
      color: var(--text-primary);
      font-family: system-ui, -apple-system, sans-serif;
      padding: 1.5rem;
      min-height: 100vh;
    }
    .chart-container {
      background: var(--bg-primary);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      max-width: 500px;
    }
    h2 {
      font-size: 1.25rem;
      margin-bottom: 1.5rem;
    }
    .bar-group {
      margin-bottom: 1rem;
    }
    .bar-label {
      font-size: 0.875rem;
      color: var(--text-secondary);
      margin-bottom: 0.25rem;
    }
    .bar-wrapper {
      background: var(--bg-tertiary);
      border-radius: 0.25rem;
      height: 2rem;
      overflow: hidden;
      position: relative;
    }
    .bar {
      background: var(--accent);
      height: 100%;
      border-radius: 0.25rem;
      transition: width 0.3s ease;
    }
    .bar-value {
      position: absolute;
      right: 0.5rem;
      top: 50%;
      transform: translateY(-50%);
      font-size: 0.875rem;
      font-weight: 600;
      color: var(--text-primary);
    }
  </style>
</head>
<body>
  <div class="chart-container">
    <h2>Project Comparison</h2>
    
    <div class="bar-group">
      <div class="bar-label">Project A - Completion</div>
      <div class="bar-wrapper">
        <div class="bar" style="width: 75%"></div>
        <span class="bar-value">75%</span>
      </div>
    </div>
    
    <div class="bar-group">
      <div class="bar-label">Project B - Completion</div>
      <div class="bar-wrapper">
        <div class="bar" style="width: 45%"></div>
        <span class="bar-value">45%</span>
      </div>
    </div>
    
    <div class="bar-group">
      <div class="bar-label">Project C - Completion</div>
      <div class="bar-wrapper">
        <div class="bar" style="width: 90%"></div>
        <span class="bar-value">90%</span>
      </div>
    </div>
    
    <div class="bar-group">
      <div class="bar-label">Project D - Completion</div>
      <div class="bar-wrapper">
        <div class="bar" style="width: 60%"></div>
        <span class="bar-value">60%</span>
      </div>
    </div>
  </div>
  
  <script>
    function sendHeight() {
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'artifact-height', height: document.body.scrollHeight }, '*');
      }
    }
    window.addEventListener('load', sendHeight);
    window.addEventListener('resize', sendHeight);
  </script>
</body>
</html>
```
````

## Example 3: Simple Quiz Widget

A multiple choice quiz with immediate feedback.

````markdown
```html:interactive
<!DOCTYPE html>
<html>
<head>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      background: var(--bg-secondary);
      color: var(--text-primary);
      font-family: system-ui, -apple-system, sans-serif;
      padding: 1.5rem;
      min-height: 100vh;
    }
    .quiz {
      background: var(--bg-primary);
      border: 1px solid var(--border);
      border-radius: 0.75rem;
      padding: 1.5rem;
      max-width: 400px;
    }
    h2 {
      font-size: 1.25rem;
      margin-bottom: 1rem;
    }
    .question {
      font-size: 1rem;
      color: var(--text-primary);
      margin-bottom: 1rem;
    }
    .options {
      display: flex;
      flex-direction: column;
      gap: 0.5rem;
    }
    .option {
      padding: 0.75rem 1rem;
      background: var(--bg-tertiary);
      border: 1px solid var(--border);
      border-radius: 0.5rem;
      cursor: pointer;
      transition: all 0.2s ease;
    }
    .option:hover {
      border-color: var(--accent);
    }
    .option.correct {
      background: var(--status-success);
      color: white;
      border-color: var(--status-success);
    }
    .option.incorrect {
      background: var(--status-error);
      color: white;
      border-color: var(--status-error);
    }
    .feedback {
      margin-top: 1rem;
      padding: 0.75rem;
      border-radius: 0.5rem;
      font-size: 0.875rem;
    }
    .feedback.correct {
      background: var(--status-success);
      color: white;
    }
    .feedback.incorrect {
      background: var(--status-error);
      color: white;
    }
    .score {
      margin-top: 1rem;
      font-size: 1.25rem;
      font-weight: 600;
      text-align: center;
    }
  </style>
</head>
<body>
  <div class="quiz">
    <h2>Quick Quiz</h2>
    <div class="question">What is the capital of France?</div>
    <div class="options">
      <div class="option" data-answer="london">London</div>
      <div class="option" data-answer="paris">Paris</div>
      <div class="option" data-answer="berlin">Berlin</div>
      <div class="option" data-answer="madrid">Madrid</div>
    </div>
    <div class="feedback" style="display: none;"></div>
    <div class="score"></div>
  </div>
  
  <script>
    let score = 0;
    let answered = false;
    
    const options = document.querySelectorAll('.option');
    const feedback = document.querySelector('.feedback');
    const scoreDisplay = document.querySelector('.score');
    
    options.forEach(option => {
      option.addEventListener('click', () => {
        if (answered) return;
        
        answered = true;
        const answer = option.dataset.answer;
        
        if (answer === 'paris') {
          option.classList.add('correct');
          feedback.textContent = 'Correct! Paris is the capital of France.';
          feedback.className = 'feedback correct';
          feedback.style.display = 'block';
          score++;
        } else {
          option.classList.add('incorrect');
          feedback.textContent = 'Incorrect. The correct answer is Paris.';
          feedback.className = 'feedback incorrect';
          feedback.style.display = 'block';
          
          options.forEach(opt => {
            if (opt.dataset.answer === 'paris') {
              opt.classList.add('correct');
            }
          });
        }
        
        scoreDisplay.textContent = 'Score: ' + score + '/1';
      });
    });
    
    function sendHeight() {
      if (window.parent !== window) {
        window.parent.postMessage({ type: 'artifact-height', height: document.body.scrollHeight }, '*');
      }
    }
    window.addEventListener('load', sendHeight);
    window.addEventListener('resize', sendHeight);
  </script>
</body>
</html>
```
````

## Theme Variables

All artifacts should use CSS custom properties for theme-aware styling:

| Variable | Description |
|----------|-------------|
| `--bg-primary` | Main background color |
| `--bg-secondary` | Secondary background |
| `--bg-tertiary` | Tertiary background |
| `--text-primary` | Primary text color |
| `--text-secondary` | Secondary text color |
| `--text-muted` | Muted text color |
| `--accent` | Accent color |
| `--accent-hover` | Accent hover color |
| `--border` | Border color |
| `--border-secondary` | Secondary border |
| `--status-success` | Success color |
| `--status-warning` | Warning color |
| `--status-error` | Error color |

## Best Practices

1. **Keep it self-contained**: All CSS and JS must be inline
2. **Use theme variables**: Ensure dark/light theme compatibility
3. **Include resize handler**: Call `postMessage` with height on load and resize
4. **Keep it small**: Aim for under 50KB for fast rendering
5. **No external resources**: No CDN links, external images, or external scripts