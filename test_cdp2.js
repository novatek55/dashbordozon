const puppeteer = require('puppeteer-core');

const BROWSER_WS_URL = 'ws://127.0.0.1:18792/devtools/browser?token=codex-browser-relay-dev-token';

async function checkDashboard() {
    console.log('Подключаюсь к Browser CDP...');
    console.log('URL:', BROWSER_WS_URL);
    
    const browser = await puppeteer.connect({
        browserWSEndpoint: BROWSER_WS_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    console.log('✅ Подключено!');
    
    const pages = await browser.pages();
    console.log(`📄 Найдено страниц: ${pages.length}`);
    
    // Выводим информацию о страницах
    for (let i = 0; i < pages.length; i++) {
        const url = await pages[i].url();
        const title = await pages[i].title();
        console.log(`  [${i}] ${title} - ${url}`);
    }
    
    // Ищем или создаем страницу с дашбордом
    let page = pages.find(p => {
        const url = p.url();
        return url.includes('127.0.0.1:8088') || url.includes('localhost:8088');
    });
    
    if (!page) {
        console.log('\n🆕 Создаю новую страницу...');
        page = await browser.newPage();
    } else {
        console.log('\n✓ Найдена существующая страница');
    }
    
    // Переходим на дашборд
    console.log('🌐 Перехожу на http://127.0.0.1:8088/orders_dashboard.html');
    await page.goto('http://127.0.0.1:8088/orders_dashboard.html', { 
        waitUntil: 'networkidle2',
        timeout: 30000 
    });
    
    console.log('⏳ Жду загрузки...');
    await page.waitForTimeout(3000);
    
    // Делаем скриншот
    console.log('📸 Делаю скриншот начального состояния...');
    await page.screenshot({ 
        path: 'E:\\скрипты OZ\\ozonapi\\screenshot_1_initial.png', 
        fullPage: true 
    });
    
    // Проверяем наличие селектора отчетов
    const reportTypeSelect = await page.$('#report_type');
    console.log(`\n✓ Select #report_type найден: ${!!reportTypeSelect}`);
    
    if (reportTypeSelect) {
        // Выбираем "Поставка"
        console.log('📝 Выбираю отчет "Поставка"...');
        await reportTypeSelect.select('supply_plan');
        await page.waitForTimeout(4000);
        
        await page.screenshot({ 
            path: 'E:\\скрипты OZ\\ozonapi\\screenshot_2_supply_plan.png', 
            fullPage: true 
        });
        console.log('📸 Скриншот: screenshot_2_supply_plan.png');
        
        // Проверяем наличие кнопки паллетизации
        const pageContent = await page.content();
        const hasPalletButton = pageContent.includes('Рассчитать паллеты') || 
                               pageContent.includes('calculatePallets');
        console.log(`✓ Кнопка "Рассчитать паллеты" найдена: ${hasPalletButton}`);
        
        // Ищем поля ввода supply_stock
        const supplyInputs = await page.$$('.supply-input');
        console.log(`📊 Полей ввода .supply-input найдено: ${supplyInputs.length}`);
        
        if (supplyInputs.length > 0) {
            console.log('\n📝 Заполняю первое поле остатка для поставки (100)...');
            
            // Очищаем и вводим значение
            await supplyInputs[0].click({ clickCount: 3 }); // Тройной клик для выделения
            await supplyInputs[0].type('100');
            
            // Убираем фокус для вызова события change
            await page.keyboard.press('Tab');
            await page.waitForTimeout(3000);
            
            await page.screenshot({ 
                path: 'E:\\скрипты OZ\\ozonapi\\screenshot_3_after_input.png', 
                fullPage: true 
            });
            console.log('📸 Скриншот: screenshot_3_after_input.png');
            
            // Проверяем пересчиталась ли таблица
            const hasDetails = await page.$eval('.analytics-details-row', el => !!el).catch(() => false);
            console.log(`✓ Таблица деталей отображается: ${hasDetails}`);
        }
        
        // Кликаем по кнопке паллетизации если она есть
        if (hasPalletButton) {
            console.log('\n🖱️ Ищу и нажимаю кнопку "Рассчитать паллеты"...');
            
            // Ищем по тексту
            const palletBtn = await page.$x("//div[contains(text(), 'Рассчитать паллеты') or contains(text(), 'Паллетизация')]");
            
            if (palletBtn.length > 0) {
                await palletBtn[0].click();
                console.log('✅ Кнопка нажата!');
                await page.waitForTimeout(3000);
                
                await page.screenshot({ 
                    path: 'E:\\скрипты OZ\\ozonapi\\screenshot_4_pallets.png', 
                    fullPage: true 
                });
                console.log('📸 Скриншот: screenshot_4_pallets.png');
                
                // Проверяем модальное окно
                const modal = await page.$('#pallet-modal');
                const modalVisible = modal ? await page.evaluate(el => el.style.display, modal) : 'not found';
                console.log(`✓ Модальное окно #pallet-modal: ${modalVisible}`);
            } else {
                console.log('❌ Кнопка не найдена по xpath');
                
                // Пробуем по onclick
                const btnByOnclick = await page.$('[onclick*="calculatePallets"]');
                if (btnByOnclick) {
                    console.log('🖱️ Нажимаю кнопку по onclick...');
                    await btnByOnclick.click();
                    await page.waitForTimeout(3000);
                    
                    await page.screenshot({ 
                        path: 'E:\\скрипты OZ\\ozonapi\\screenshot_4_pallets.png', 
                        fullPage: true 
                    });
                    console.log('📸 Скриншот: screenshot_4_pallets.png');
                }
            }
        }
    }
    
    // Получаем консольные ошибки
    const consoleErrors = await page.evaluate(() => {
        if (window.consoleErrors) return window.consoleErrors;
        return [];
    });
    
    if (consoleErrors.length > 0) {
        console.log('\n⚠️ Ошибки в консоли:');
        consoleErrors.forEach(err => console.log(`  - ${err}`));
    }
    
    await browser.disconnect();
    console.log('\n✅ Проверка завершена!');
}

checkDashboard().catch(err => {
    console.error('❌ Ошибка:', err.message);
    console.error(err.stack);
    process.exit(1);
});
