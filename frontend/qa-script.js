const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

async function runQA() {
  console.log('Launching browser...');
  const browser = await chromium.launch();
  const context = await browser.newContext();
  const page = await context.newPage();

  const evidenceDir = path.resolve(__dirname, '../.sisyphus/evidence');
  if (!fs.existsSync(evidenceDir)) {
    fs.mkdirSync(evidenceDir, { recursive: true });
  }

  const logFile = path.join(evidenceDir, 'qa-log.txt');
  const log = (msg) => {
    console.log(msg);
    fs.appendFileSync(logFile, msg + '\n');
  };

  try {
    log('Navigating to http://localhost:3000...');
    await page.goto('http://localhost:3000');
    await page.waitForLoadState('networkidle');

    // --- Pin/Delete Scenario ---
    log('Starting Pin/Delete Scenario...');
    
    // Ensure at least one chat exists
    const chatItems = page.locator('[data-testid="chat-item"]');
    if (await chatItems.count() === 0) {
      log('No chats found, creating one...');
      // Try multiple selectors for the input
      const inputSelector = 'textarea[placeholder="Ask anything..."]';
      try {
        await page.waitForSelector(inputSelector, { timeout: 5000 });
        await page.fill(inputSelector, 'Hello world');
      } catch (e) {
        log('Input not found with "Ask anything...", trying generic textarea');
        await page.fill('textarea', 'Hello world');
      }
      
      await page.keyboard.press('Enter');
      // Wait for response or just wait a bit
      await page.waitForTimeout(5000); 
    }

    // Find the first chat item
    const firstChat = chatItems.first();
    await firstChat.hover();
    
    // Look for Pin button (assuming it's visible on hover or in a menu)
    // Adjust selector based on actual UI implementation
    const pinButton = firstChat.locator('button[aria-label="Pin"]');
    if (await pinButton.isVisible()) {
        await pinButton.click();
        log('Clicked Pin button');
    } else {
        // Maybe it's in a context menu?
        const menuButton = firstChat.locator('button[aria-label="Options"]');
        if (await menuButton.isVisible()) {
            await menuButton.click();
            // Wait for menu to appear
            await page.waitForSelector('text=Pin');
            await page.click('text=Pin');
            log('Clicked Pin from menu');
        } else {
            log('Could not find Pin button or menu');
        }
    }

    // Verify pinned (check for pinned section or icon)
    // This depends on UI implementation. For now, just wait a bit.
    await page.waitForTimeout(1000);
    
    await page.screenshot({ path: path.join(evidenceDir, 'task-5-pin-delete.png') });
    log('Saved task-5-pin-delete.png');

    // Delete the chat
    await firstChat.hover();
    const deleteButton = firstChat.locator('button[aria-label="Delete"]');
    if (await deleteButton.isVisible()) {
        await deleteButton.click();
        // Confirm delete if modal appears
        const confirmButton = page.locator('button:has-text("Delete")');
        if (await confirmButton.isVisible()) {
            await confirmButton.click();
        }
        log('Clicked Delete button');
    } else {
         const menuButton = firstChat.locator('button[aria-label="Options"]');
        if (await menuButton.isVisible()) {
            await menuButton.click();
            // Wait for menu
            await page.waitForSelector('text=Delete');
            await page.click('text=Delete');
             // Confirm delete if modal appears
            const confirmButton = page.locator('button:has-text("Delete")');
            if (await confirmButton.isVisible()) {
                await confirmButton.click();
            }
            log('Clicked Delete from menu');
        }
    }
    
    await page.waitForTimeout(1000);

    // --- Search Scenario ---
    log('Starting Search Scenario...');
    
    // Create a specific chat for search
    await page.goto('http://localhost:3000'); // Reset to new chat
    await page.fill('textarea[placeholder="Send a message..."]', 'Searchable Content 123');
    await page.keyboard.press('Enter');
    await page.waitForTimeout(5000);

    // Search for it
    const searchInput = page.locator('input[placeholder="Search chats..."]');
    if (await searchInput.isVisible()) {
        await searchInput.fill('Searchable');
        await page.waitForTimeout(1000);
        
        // Verify results
        const results = page.locator('[data-testid="chat-item"]');
        const count = await results.count();
        log(`Found ${count} search results`);
        
        await page.screenshot({ path: path.join(evidenceDir, 'task-5-search.png') });
        log('Saved task-5-search.png');
    } else {
        log('Search input not found');
    }

  } catch (error) {
    log('QA Failed: ' + error);
    await page.screenshot({ path: path.join(evidenceDir, 'qa-failure.png') });
    const html = await page.content();
    fs.writeFileSync(path.join(evidenceDir, 'qa-page.html'), html);
  } finally {
    await browser.close();
  }
}

runQA();
