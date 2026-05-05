const puppeteer = require('puppeteer-core');

const BROWSER_WS_URL = 'ws://127.0.0.1:18792/devtools/browser?token=codex-browser-relay-dev-token';

async function testPalletization() {
    console.log('🚀 Подключаюсь к CDP...');
    
    const browser = await puppeteer.connect({
        browserWSEndpoint: BROWSER_WS_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    console.log('✅ Подключено!');
    
    // Создаем новую страницу
    const page = await browser.newPage();
    
    // Переходим на дашборд
    console.log('🌐 Открываю http://127.0.0.1:8088/orders_dashboard.html');
    await page.goto('http://127.0.0.1:8088/orders_dashboard.html', { 
        waitUntil: 'networkidle2',
        timeout: 30000 
    });
    
    console.log('⏳ Жду загрузки...');
    await page.waitForTimeout(2000);
    
    // Делаем скриншот начального состояния
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_01_initial.png', fullPage: true });
    console.log('📸 Скриншот 1: cdp_01_initial.png');
    
    // Включаем логи консоли
    page.on('console', msg => console.log('📝 Console:', msg.text()));
    page.on('pageerror', error => console.log('❌ Page Error:', error.message));
    
    // Выбираем отчет "Поставка"
    console.log('📝 Выбираю отчет "Поставка"...');
    await page.select('#report_type', 'supply_plan');
    await page.waitForTimeout(4000);
    
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_02_supply_plan.png', fullPage: true });
    console.log('📸 Скриншот 2: cdp_02_supply_plan.png');
    
    // Проверяем наличие кнопки паллетизации
    const hasPalletBtn = await page.evaluate(() => {
        const btn = document.querySelector('[onclick*="calculatePalletsForSupplyPlan"]');
        if (btn) {
            console.log('Кнопка паллетизации найдена:', btn.textContent);
            return { found: true, text: btn.textContent.trim() };
        }
        return { found: false };
    });
    
    console.log('🔍 Кнопка паллетизации:', hasPalletBtn);
    
    // Ищем поля ввода
    const inputs = await page.$$('.supply-input');
    console.log(`📊 Найдено полей .supply-input: ${inputs.length}`);
    
    if (inputs.length > 0) {
        // Заполняем первое поле
        console.log('📝 Заполняю первое поле: 100');
        await inputs[0].click();
        await inputs[0].type('100');
        await page.keyboard.press('Tab');
        await page.waitForTimeout(3000);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_03_after_input.png', fullPage: true });
        console.log('📸 Скриншот 3: cdp_03_after_input.png');
        
        // Проверяем наличие распределения
        const hasDetails = await page.$eval('.analytics-details-row', el => !!el).catch(() => false);
        console.log(`✓ Таблица деталей: ${hasDetails}`);
    }
    
    // Ищем и нажимаем кнопку паллетизации
    console.log('🖱️ Ищу кнопку "Рассчитать паллеты"...');
    
    const palletBtn = await page.$('[onclick*="calculatePalletsForSupplyPlan"]');
    
    if (palletBtn) {
        console.log('✅ Кнопка найдена! Нажимаю...');
        await palletBtn.click();
        await page.waitForTimeout(2000);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_04_clicked.png', fullPage: true });
        console.log('📸 Скриншот 4: cdp_04_clicked.png (после клика)');
        
        // Ждем загрузки
        await page.waitForTimeout(3000);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_05_after_wait.png', fullPage: true });
        console.log('📸 Скриншот 5: cdp_05_after_wait.png (через 3 сек)');
        
        // Проверяем модальное окно
        const modalInfo = await page.evaluate(() => {
            const modal = document.getElementById('pallet-modal');
            const loading = document.getElementById('pallet-loading');
            const results = document.getElementById('pallet-results');
            
            return {
                modalExists: !!modal,
                modalDisplay: modal ? modal.style.display : 'not found',
                loadingDisplay: loading ? loading.style.display : 'not found',
                resultsContent: results ? results.innerHTML.substring(0, 500) : 'not found'
            };
        });
        
        console.log('🔍 Модальное окно:', modalInfo);
        
        // Ждем еще немного
        await page.waitForTimeout(3000);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\cdp_06_final.png', fullPage: true });
        console.log('📸 Скриншот 6: cdp_06_final.png (итоговый)');
        
    } else {
        console.log('❌ Кнопка не найдена!');
        
        // Пробуем найти по тексту
        const byText = await page.$x("//div[contains(text(), 'Рассчитать')]");
        console.log(`Найдено по тексту 'Рассчитать': ${byText.length}`);
        
        const byText2 = await page.$x("//div[contains(text(), 'Паллетизация')]");
        console.log(`Найдено по тексту 'Паллетизация': ${byText2.length}`);
    }
    
    await browser.disconnect();
    console.log('\n✅ Проверка завершена!');
}

testPalletization().catch(err => {
    console.error('❌ Ошибка:', err.message);
    console.error(err.stack);
    process.exit(1);
});
