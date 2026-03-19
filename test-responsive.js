const { chromium } = require('playwright');

async function runTests() {
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/chromium'
  });
  
  const results = [];
  const viewports = [375, 768, 1024];
  const routes = ['/', '/settings/profile'];
  
  for (const route of routes) {
    for (const width of viewports) {
      console.log(`Testing ${route} at ${width}px...`);
      
      const page = await browser.newPage({
        viewport: { width, height: 800 }
      });
      
      const errors = [];
      page.on('console', msg => {
        if (msg.type() === 'error') {
          errors.push(msg.text());
        }
      });
      
      try {
        await page.goto(`http://localhost:3000${route}`, { 
          waitUntil: 'networkidle',
          timeout: 30000 
        });
        
        // Check for horizontal overflow
        const overflow = await page.evaluate(() => {
          const body = document.body;
          return {
            scrollWidth: body.scrollWidth,
            innerWidth: window.innerWidth,
            hasOverflow: body.scrollWidth > window.innerWidth
          };
        });
        
        const issues = [];
        if (overflow.hasOverflow) {
          issues.push(`horizontal-overflow: scrollWidth=${overflow.scrollWidth}, innerWidth=${overflow.innerWidth}`);
        }
        
        const status = issues.length === 0 && errors.length === 0 ? 'PASS' : 'FAIL';
        
        results.push({
          route,
          width,
          status,
          issues,
          errors
        });
        
        console.log(`  → ${status}`);
        if (issues.length > 0) issues.forEach(i => console.log(`    - ${i}`));
        if (errors.length > 0) errors.forEach(e => console.log(`    - console: ${e}`));
        
      } catch (e) {
        results.push({
          route,
          width,
          status: 'ERROR',
          issues: [e.message],
          errors: []
        });
        console.log(`  → ERROR: ${e.message}`);
      } finally {
        await page.close();
      }
    }
  }
  
  console.log('\n=== RESULTS ===');
  for (const r of results) {
    console.log(`${r.route} @ ${r.width}px: ${r.status}`);
  }
  
  const pass = results.filter(r => r.status === 'PASS').length;
  const fail = results.filter(r => r.status !== 'PASS').length;
  console.log(`\nPass: ${pass}/${results.length}, Fail: ${fail}/${results.length}`);
  
  await browser.close();
  
  return results;
}

runTests().catch(console.error);