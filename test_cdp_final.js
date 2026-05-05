const puppeteer = require('puppeteer-core');

const BROWSER_WS_URL = 'ws://127.0.0.1:18792/devtools/browser?token=codex-browser-relay-dev-token';

async function test() {
    console.log('Connecting to CDP...');
    const browser = await puppeteer.connect({
        browserWSEndpoint: BROWSER_WS_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    // Получаем страницы
    let pages = await browser.pages();
    console.log(`Pages: ${pages.length}`);
    
    // Используем CDP напрямую для создания цели
    const cdp = await browser.target().createCDPSession();
    const { targetId } = await cdp.send('Target.createTarget', {
        url: 'http://127.0.0.1:8088/',
        width: 1920,
        height: 1080
    });
    console.log(`Created target: ${targetId}`);
    
    // Ждем
    await new Promise(r => setTimeout(r, 5000));
    
    // Получаем обновленный список страниц
    pages = await browser.pages();
    console.log(`Pages now: ${pages.length}`);
    
    const page = pages[pages.length - 1];
    
    // Логи
    page.on('console', msg => console.log('CONSOLE:', msg.text()));
    page.on('pageerror', err => console.log('ERROR:', err.message));
    
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\final_01.png', fullPage: true });
    console.log('Screenshot: final_01.png');
    
    // Выбираем Поставку
    console.log('Selecting supply_plan...');
    await page.select('#report_type', 'supply_plan');
    await page.waitForTimeout(5000);
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\final_02_supply.png', fullPage: true });
    console.log('Screenshot: final_02_supply.png');
    
    // Проверяем кнопку
    const btnCheck = await page.evaluate(() => {
        const btn = document.querySelector('[onclick*="calculatePallets"]');
        if (btn) {
            btn.scrollIntoView();
            return { found: true, onclick: btn.getAttribute('onclick') };
        }
        return { found: false };
    });
    console.log('Button:', btnCheck);
    
    // Заполняем поля
    const inputs = await page.$$('.supply-input');
    console.log(`Inputs found: ${inputs.length}`);
    
    if (inputs.length > 0) {
        for (let i = 0; i < Math.min(2, inputs.length); i++) {
            await inputs[i].click();
            await inputs[i].type('100');
        }
        await page.keyboard.press('Tab');
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\final_03_filled.png', fullPage: true });
        console.log('Screenshot: final_03_filled.png');
    }
    
    // Кликаем по кнопке
    const btn = await page.$('[onclick*="calculatePallets"]');
    if (btn) {
        console.log('Clicking button...');
        await btn.click();
        await page.waitForTimeout(2000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\final_04_click.png', fullPage: true });
        console.log('Screenshot: final_04_click.png');
        
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\final_05_wait.png', fullPage: true });
        console.log('Screenshot: final_05_wait.png');
        
        // Проверяем модалку
        const modal = await page.evaluate(() => {
            const m = document.getElementById('pallet-modal');
            const r = document.getElementById('pallet-results');
            return {
                modalExists: !!m,
                modalDisplay: m ? m.style.display : null,
                resultsHTML: r ? r.innerHTML.substring(0, 500) : null
            };
        });
        console.log('Modal:', modal);
    }
    
    await browser.disconnect();
    console.log('Done!');
}

test().catch(err => {
    console.error('Error:', err.message);
    process.exit(1);
});
