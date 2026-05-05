const puppeteer = require('puppeteer-core');

const CDP_URL = 'ws://127.0.0.1:18792/cdp?token=codex-browser-relay-dev-token';

async function checkDashboard() {
    console.log('Подключаюсь к CDP...');
    
    const browser = await puppeteer.connect({
        browserWSEndpoint: CDP_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    console.log('Подключено! Получаю страницы...');
    const pages = await browser.pages();
    console.log(`Найдено страниц: ${pages.length}`);
    
    // Ищем страницу с Ozon Dashboard
    let dashboardPage = null;
    for (const page of pages) {
        const url = await page.url();
        const title = await page.title();
        console.log(`  - ${title}: ${url}`);
        if (url.includes('127.0.0.1:8088') || title.includes('Ozon')) {
            dashboardPage = page;
        }
    }
    
    if (!dashboardPage) {
        console.log('Dashboard не найден, открываю новую страницу...');
        dashboardPage = await browser.newPage();
        await dashboardPage.goto('http://127.0.0.1:8088/orders_dashboard.html', { waitUntil: 'networkidle2' });
    } else {
        console.log('Активирую существующую страницу...');
        await dashboardPage.bringToFront();
    }
    
    // Ждем загрузки
    await dashboardPage.waitForTimeout(2000);
    
    // Делаем скриншот
    console.log('Делаю скриншот...');
    await dashboardPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_screenshot_1.png', fullPage: true });
    console.log('Скриншот сохранен: cdp_screenshot_1.png');
    
    // Проверяем наличие select с report_type
    const hasReportSelect = await dashboardPage.$('#report_type') !== null;
    console.log(`Select #report_type найден: ${hasReportSelect}`);
    
    if (hasReportSelect) {
        // Выбираем "Поставка"
        console.log('Выбираю отчет "Поставка"...');
        await dashboardPage.select('#report_type', 'supply_plan');
        await dashboardPage.waitForTimeout(3000);
        
        // Скриншот после выбора
        await dashboardPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_screenshot_2_supply_plan.png', fullPage: true });
        console.log('Скриншот сохранен: cdp_screenshot_2_supply_plan.png');
        
        // Проверяем наличие кнопки паллетизации
        const palletButton = await dashboardPage.$eval('.analytics-summary-card[style*="palletization"], .analytics-summary-card[style*="Паллетизация"], .analytics-summary-card[onclick*="calculatePallets"]', el => el.textContent).catch(() => null);
        console.log(`Кнопка паллетизации: ${palletButton || 'НЕ НАЙДЕНА'}`);
        
        // Ищем поля ввода supply_stock
        const supplyInputs = await dashboardPage.$$eval('.supply-input', inputs => inputs.length);
        console.log(`Полей ввода supply_stock: ${supplyInputs}`);
        
        if (supplyInputs > 0) {
            // Заполняем первое поле
            console.log('Заполняю первое поле остатка для поставки...');
            await dashboardPage.type('.supply-input', '100');
            await dashboardPage.waitForTimeout(2000);
            
            // Скриншот после ввода
            await dashboardPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_screenshot_3_after_input.png', fullPage: true });
            console.log('Скриншот сохранен: cdp_screenshot_3_after_input.png');
            
            // Нажимаем Enter или убираем фокус чтобы вызвать пересчет
            await dashboardPage.keyboard.press('Tab');
            await dashboardPage.waitForTimeout(3000);
            
            await dashboardPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_screenshot_4_after_recalc.png', fullPage: true });
            console.log('Скриншот сохранен: cdp_screenshot_4_after_recalc.png');
        }
        
        // Ищем и кликаем кнопку паллетизации
        const palletBtn = await dashboardPage.$('text/Рассчитать паллеты') || 
                          await dashboardPage.$('[onclick*="calculatePallets"]');
        
        if (palletBtn) {
            console.log('Нажимаю кнопку "Рассчитать паллеты"...');
            await palletBtn.click();
            await dashboardPage.waitForTimeout(3000);
            
            await dashboardPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_screenshot_5_pallets.png', fullPage: true });
            console.log('Скриншот сохранен: cdp_screenshot_5_pallets.png');
        }
    }
    
    // Получаем HTML для анализа
    const html = await dashboardPage.content();
    console.log(`\nHTML страницы получен, длина: ${html.length}`);
    
    // Ищем ошибки в консоли
    const logs = await dashboardPage.evaluate(() => {
        return window.consoleLogs || [];
    });
    console.log(`Логи консоли: ${logs.length}`);
    
    await browser.disconnect();
    console.log('\n✅ Проверка завершена!');
}

checkDashboard().catch(err => {
    console.error('❌ Ошибка:', err);
    process.exit(1);
});
