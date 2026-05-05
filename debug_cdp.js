const puppeteer = require('puppeteer-core');

const BROWSER_WS_URL = 'ws://127.0.0.1:18792/devtools/browser?token=codex-browser-relay-dev-token';

async function debugPage() {
    console.log('🔌 Подключаюсь к CDP...');
    const browser = await puppeteer.connect({
        browserWSEndpoint: BROWSER_WS_URL,
        defaultViewport: { width: 1920, height: 1080 }
    });
    
    console.log('✅ Подключено!');
    
    // Получаем страницы
    const pages = await browser.pages();
    console.log(`📄 Страниц: ${pages.length}`);
    
    // Ищем страницу с ozon дашбордом
    let page = null;
    for (const p of pages) {
        const url = await p.url().catch(() => '');
        console.log(`   URL: ${url}`);
        if (url.includes('8088')) {
            page = p;
            break;
        }
    }
    
    if (!page) {
        console.log('❌ Страница с 8088 не найдена, создаю новую...');
        page = await browser.newPage();
        await page.goto('http://127.0.0.1:8088/', { waitUntil: 'networkidle2' });
    }
    
    // Активируем страницу
    await page.bringToFront();
    
    // Включаем логи
    page.on('console', msg => console.log('📝 Console:', msg.text()));
    page.on('pageerror', err => console.log('❌ Error:', err.message));
    
    // Делаем скриншот
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\debug_01.png', fullPage: true });
    console.log('📸 Скриншот: debug_01.png');
    
    // Проверяем структуру HTML
    console.log('\n🔍 Анализирую структуру страницы...');
    
    const structure = await page.evaluate(() => {
        return {
            groups: document.querySelectorAll('.analytics-group').length,
            groupsAlt: document.querySelectorAll('[class*="group"]').length,
            tables: document.querySelectorAll('table').length,
            supplyTables: document.querySelectorAll('.supply-detail-table').length,
            detailTables: document.querySelectorAll('.analytics-detail-table').length,
            offerIds: Array.from(document.querySelectorAll('.analytics-offer-id')).map(el => el.textContent.trim()).slice(0, 5),
            bodyHTML: document.body.innerHTML.substring(0, 2000)
        };
    });
    
    console.log('📊 Структура:');
    console.log('   .analytics-group:', structure.groups);
    console.log('   [class*="group"]:', structure.groupsAlt);
    console.log('   tables:', structure.tables);
    console.log('   .supply-detail-table:', structure.supplyTables);
    console.log('   .analytics-detail-table:', structure.detailTables);
    console.log('   offerIds:', structure.offerIds);
    
    // Если нет групп, смотрим HTML
    if (structure.groups === 0 && structure.groupsAlt === 0) {
        console.log('\n⚠️ Группы не найдены! Смотрим HTML...');
        console.log(structure.bodyHTML);
    }
    
    // Проверяем выбран ли отчет Поставка
    const reportType = await page.evaluate(() => {
        const select = document.getElementById('report_type');
        return select ? select.value : 'not found';
    });
    console.log('\n📋 Текущий отчет:', reportType);
    
    // Если не Поставка, выбираем его
    if (reportType !== 'supply_plan') {
        console.log('📝 Выбираю "Поставка"...');
        await page.select('#report_type', 'supply_plan');
        await page.waitForTimeout(5000);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\debug_02_supply.png', fullPage: true });
        console.log('📸 Скриншот: debug_02_supply.png');
    }
    
    // Снова проверяем структуру
    console.log('\n🔍 Повторный анализ...');
    const structure2 = await page.evaluate(() => {
        // Пробуем найти таблицы разными способами
        const allTables = document.querySelectorAll('table');
        const tableInfo = [];
        
        allTables.forEach((table, i) => {
            const rows = table.querySelectorAll('tr');
            const firstRow = rows[0];
            const cells = firstRow ? firstRow.querySelectorAll('td, th') : [];
            tableInfo.push({
                index: i,
                rows: rows.length,
                cells: cells.length,
                className: table.className,
                parentClass: table.parentElement?.className?.substring(0, 50)
            });
        });
        
        return {
            groups: document.querySelectorAll('.analytics-group').length,
            allTables: allTables.length,
            tableInfo: tableInfo
        };
    });
    
    console.log('📊 Структура после выбора Поставка:');
    console.log('   Группы:', structure2.groups);
    console.log('   Таблиц:', structure2.allTables);
    console.log('   Инфо о таблицах:', JSON.stringify(structure2.tableInfo, null, 2));
    
    // Пробуем нажать кнопку паллетизации
    console.log('\n🖱️ Ищу кнопку паллетизации...');
    const btnInfo = await page.evaluate(() => {
        const btn = document.querySelector('[onclick*="calculatePallets"]');
        if (btn) {
            return {
                found: true,
                text: btn.innerText,
                onclick: btn.getAttribute('onclick')
            };
        }
        return { found: false };
    });
    console.log('Кнопка:', btnInfo);
    
    if (btnInfo.found) {
        console.log('🖱️ Нажимаю кнопку...');
        const btn = await page.$('[onclick*="calculatePallets"]');
        await btn.click();
        
        await page.waitForTimeout(3000);
        
        // Получаем логи консоли
        const logs = await page.evaluate(() => {
            return window.consoleLogs || [];
        });
        console.log('\n📋 Логи консоли:', logs);
        
        await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\debug_03_click.png', fullPage: true });
        console.log('📸 Скриншот: debug_03_click.png');
    }
    
    await browser.disconnect();
    console.log('\n✅ Готово!');
}

debugPage().catch(err => {
    console.error('❌ Ошибка:', err.message);
    process.exit(1);
});
