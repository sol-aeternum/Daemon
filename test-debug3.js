const { chromium } = require('playwright');

async function debug() {
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/chromium'
  });
  
  const page = await browser.newPage({
    viewport: { width: 375, height: 800 }
  });
  
  page.on('response', async (res) => {
    if (res.status() === 404) {
      console.log('404:', res.url());
    }
  });
  
  await page.goto(`http://localhost:3000/settings/profile`, { 
    waitUntil: 'networkidle',
    timeout: 30000 
  });
  
  // Wait more for any async resources
  await page.waitForTimeout(3000);
  
  await browser.close();
}

debug().catch(console.error);