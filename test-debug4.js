const { chromium } = require('playwright');

async function debug() {
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/chromium'
  });
  
  const page = await browser.newPage({
    viewport: { width: 375, height: 800 }
  });
  
  page.on('requestfailed', request => {
    console.log('FAILED:', request.url(), request.failure()?.errorText);
  });
  
  page.on('response', res => {
    if (res.status() >= 400) {
      console.log(res.status(), res.url());
    }
  });
  
  await page.goto(`http://localhost:3000/settings/profile`, { 
    waitUntil: 'networkidle',
    timeout: 30000 
  });
  
  await page.waitForTimeout(2000);
  
  await browser.close();
}

debug().catch(console.error);