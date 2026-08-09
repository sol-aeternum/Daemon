import { chromium } from 'playwright';

const BASE_URL = 'http://localhost:3000';

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();

page.on('console', (msg) => {
  console.log('BROWSER CONSOLE', msg.type(), msg.text());
});

page.on('request', (request) => {
  if (request.url().includes('/api/chat')) {
    const body = request.postData() || '';
    console.log('REQUEST /api/chat', body.slice(0, 400));
  }
});

page.on('response', async (response) => {
  if (response.url().includes('/api/chat')) {
    console.log('RESPONSE /api/chat', response.status());
    try {
      const text = await response.text();
      console.log('RESPONSE BODY /api/chat', text.slice(0, 1200));
    } catch (error) {
      console.log('RESPONSE BODY READ ERROR', String(error));
    }
  }
});

await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
await page
  .getByPlaceholder('Message Daemon...')
  .waitFor({ state: 'visible', timeout: 45000 });

const newChatButton = page.getByRole('button', { name: 'New chat' });
if (await newChatButton.isVisible().catch(() => false)) {
  await newChatButton.click();
  await page.waitForTimeout(1000);
}

const input = page.getByPlaceholder('Message Daemon...');
await input.click();
await page.keyboard.press('ControlOrMeta+A');
await page.keyboard.press('Backspace');
await page.keyboard.type('/council debug message');

const valueAfterType = await input.inputValue().catch(() => '<read-error>');
console.log('VALUE AFTER TYPE', JSON.stringify(valueAfterType));

const sendButton = page.getByRole('button', { name: 'Send message' });
console.log('SEND DISABLED BEFORE CLICK', await sendButton.isDisabled());
if (await sendButton.isDisabled()) {
  await input.fill('/council debug message');
  const valueAfterFill = await input.inputValue().catch(() => '<read-error>');
  console.log('VALUE AFTER FILL', JSON.stringify(valueAfterFill));
  console.log('SEND DISABLED AFTER FILL', await sendButton.isDisabled());
}
await sendButton.click({ timeout: 45000 });

await page.waitForTimeout(12000);

const interviewVisible = await page
  .getByRole('heading', { name: 'Council Configuration' })
  .isVisible()
  .catch(() => false);

if (interviewVisible) {
  await page
    .getByRole('button', { name: 'Use Defaults' })
    .click()
    .catch(() => {});
}

await page.waitForTimeout(20000);

const progressVisible = await page
  .getByText('Council Deliberation', { exact: false })
  .isVisible()
  .catch(() => false);

const outputVisible = await page
  .getByRole('heading', { name: 'Council Output' })
  .isVisible()
  .catch(() => false);

const userBubbleCount = await page
  .getByText('/council debug message', { exact: false })
  .count();

const bodyText = await page.locator('body').innerText();

console.log('INTERVIEW_VISIBLE', interviewVisible);
console.log('PROGRESS_VISIBLE', progressVisible);
console.log('OUTPUT_VISIBLE', outputVisible);
console.log('USER_BUBBLE_COUNT', userBubbleCount);
console.log(
  'BODY HAS COUNCIL CONFIG',
  bodyText.includes('Council Configuration'),
);
console.log(
  'BODY HAS COUNCIL DELIBERATION',
  bodyText.includes('Council Deliberation'),
);
console.log('BODY HAS API KEY REQUIRED', bodyText.includes('API key required'));

await page.screenshot({
  path: '../.sisyphus/evidence/debug-council-playwright.png',
  fullPage: true,
});
await browser.close();
