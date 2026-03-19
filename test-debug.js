const { chromium } = require('playwright');

async function debug() {
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/chromium'
  });
  
  const page = await browser.newPage({
    viewport: { width: 375, height: 800 }
  });
  
  const requests = [];
  page.on('request', req => {
    if (req.resourceType() !== 'document') {
      requests.push({ url: req.url(), method: req.method() });
    }
  });
  
  page.on('response', res => {
    if (res.status() >= 400) {
      const idx = requests.findIndex(r => r.url === res.url());
      if (idx >= 0) {
        requests[idx].status = res.status();
      }
    }
  });
  
  await page.goto(`http://localhost:3000/settings/profile`, { 
    waitUntil: 'networkidle',
    timeout: 30000 
  });
  
  console.log('Requests:');
  requests.forEach(r => {
    console.log(`  ${r.status || 'ok'} - ${r.url}`);
  });
  
  await browser.close();
}

debug().catch(console.error);