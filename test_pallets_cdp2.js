const puppeteer = require('puppeteer-core');

const BROWSER_WS_URL = 'ws://127.0.0.1:18792/devtools/browser?token=codex-browser-relay-dev-token';

async function testPalletization() {
    console.log('🚀 Подключаюсь к CDP...');
    
    const browser = await puppeteer.connect({
        browserWSEndpoint: BROWSER_WS_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    console.log('✅ Подключено!');
    
    // Получаем существующие страницы
    const pages = await browser.pages();
    console.log(`📄 Страниц в браузере: ${pages.length}`);
    
    let page;
    
    // Ищем страницу с дашбордом или создаем новую
    for (const p of pages) {
        const url = await p.url().catch(() => '');
        console.log(`  - ${url}`);
        if (url.includes('8088')) {
            page = p;
            console.log('✓ Найдена существующая страница с 8088');
            break;
        }
    }
    
    if (!page) {
        console.log('🆕 Создаю новую страницу...');
        const targets = await browser.targets();
        console.log(`Целей: ${targets.length}`);
        
        // Используем CDP напрямую для создания страницы
        const cdpSession = await browser.target().createCDPSession();
        const { targetId } = await cdpSession.send('Target.createTarget', {
            url: 'http://127.0.0.1:8088/orders_dashboard.html'
        });
        console.log(`Создана страница: ${targetId}`);
        
        // Ждем загрузки
        await new Promise(r => setTimeout(r, 5000));
        
        // Получаем страницу
        const allPages = await browser.pages();
        page = allPages[allPages.length - 1];
    }
    
    // Переходим на нужный URL если нужно
    const currentUrl = await page.url().catch(() => '');
    if (!currentUrl.includes('orders_dashboard.html')) {
        console.log('🌐 Перехожу на orders_dashboard.html...');
        await page.goto('http://127.0.0.1:8088/orders_dashboard.html', { 
            waitUntil: 'domcontentloaded',
            timeout: 20000 
        });
    }
    
    console.log('⏳ Жду загрузки...');
    await page.waitForTimeout(3000);
    
    // Скриншот
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_t01.png', fullPage: true });
    console.log('📸 Скриншот: cdp_t01.png');
    
    // Логи консоли
    page.on('console', msg => console.log('📝', msg.text()));
    page.on('pageerror', err => console.log('❌', err.message));
    
    // Выбираем Поставку
    console.log('📝 Выбираю "Поставка"...');
    try {
        await page.select('#report_type', 'supply_plan');
        await page.waitForTimeout(5000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_t02_supply.png', fullPage: true });
        console.log('📸 Скриншот: cdp_t02_supply.png');
    } catch (e) {
        console.log('Ошибка выбора:', e.message);
    }
    
    // Проверяем наличие кнопки
    const btnInfo = await page.evaluate(() => {
        const btn = document.querySelector('[onclick*="calculatePallets"]');
        return {
            found: !!btn,
            text: btn ? btn.innerText : 'not found',
            onclick: btn ? btn.getAttribute('onclick') : null
        };
    });
    console.log('🔍 Кнопка:', btnInfo);
    
    // Заполняем поле если есть
    const inputs = await page.$$('.supply-input');
    if (inputs.length > 0) {
        console.log(`Заполняю ${inputs.length} полей...`);
        for (let i = 0; i < Math.min(3, inputs.length); i++) {
            await inputs[i].click();
            await inputs[i].type('50');
        }
        await page.keyboard.press('Tab');
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_t03_filled.png', fullPage: true });
        console.log('📸 Скриншот: cdp_t03_filled.png');
    }
    
    // Нажимаем кнопку
    console.log('🖱️ Нажимаю кнопку паллетизации...');
    const btn = await page.$('[onclick*="calculatePallets"]');
    if (btn) {
        await btn.click();
        console.log('✅ Клик выполнен');
        await page.waitForTimeout(2000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_t04_click.png', fullPage: true });
        console.log('📸 Скриншот: cdp_t04_click.png');
        
        await page.waitForTimeout(3000);
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_t05_final.png', fullPage: true });
        console.log('📸 Скриншот: cdp_t05_final.png');
        
        // Проверяем модалку
        const modal = await page.evaluate(() => {
            const m = document.getElementById('pallet-modal');
            return {
                exists: !!m,
                display: m ? m.style.display : 'none',
                html: m ? m.outerHTML.substring(0, 1000) : 'not found'
            };
        });
        console.log('🔍 Модалка:', modal);
    } else {
        console.log('❌ Кнопка не найдена!');
    }
    
    await browser.disconnect();
    console.log('\n✅ Готово!');
}

testPalletization().catch(err => {
    console.error('❌ Ошибка:', err.message);
    process.exit(1);
});
