const { chromium } = require('playwright');

async function debug() {
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/chromium'
  });
  
  const page = await browser.newPage({
    viewport: { width: 375, height: 800 }
  });
  
  const consoleMessages = [];
  page.on('console', msg => {
    consoleMessages.push({ type: msg.type(), text: msg.text() });
  });
  
  await page.goto(`http://localhost:3000/settings/profile`, { 
    waitUntil: 'networkidle',
    timeout: 30000 
  });
  
  // Wait a bit more for any delayed errors
  await page.waitForTimeout(2000);
  
  console.log('Console messages:');
  consoleMessages.forEach(m => {
    console.log(`  [${m.type}] ${m.text}`);
  });
  
  // Check page content
  const content = await page.content();
  console.log('\nPage has content:', content.length, 'chars');
  
  await browser.close();
}

debug().catch(console.error);