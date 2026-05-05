import puppeteer from 'puppeteer-core';

const RELAY_TOKEN = 'codex-browser-relay-dev-token';
const RELAY_URL = 'ws://127.0.0.1:18792/devtools/browser?token=' + RELAY_TOKEN;

async function checkPage() {
  console.log('🔌 Подключаюсь к браузеру...');
  
  const browser = await puppeteer.connect({
    browserWSEndpoint: RELAY_URL,
    defaultViewport: { width: 1920, height: 1080 }
  });

  console.log('✅ Подключено!');
  
  // Получаем все страницы
  const pages = await browser.pages();
  console.log(`📄 Всего страниц: ${pages.length}`);
  
  // Ищем страницу с дашбордом
  let targetPage = null;
  for (const page of pages) {
    const url = await page.url();
    const title = await page.title().catch(() => 'no title');
    console.log(`   - ${title}: ${url}`);
    if (url.includes('127.0.0.1:8088') || url.includes('localhost:8088')) {
      targetPage = page;
    }
  }
  
  if (!targetPage) {
    console.log('\n🆕 Создаю новую страницу...');
    targetPage = await browser.newPage();
    await targetPage.goto('http://127.0.0.1:8088/', { waitUntil: 'domcontentloaded', timeout: 15000 });
    await targetPage.waitForTimeout(3000);
  } else {
    console.log('\n✓ Найдена существующая страница');
    await targetPage.bringToFront();
  }
  
  // Логи
  targetPage.on('console', msg => console.log('📝', msg.text()));
  
  // Скриншот
  await targetPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\check_01.png', fullPage: true });
  console.log('📸 Скриншот: check_01.png');
  
  // Проверяем структуру
  console.log('\n🔍 Проверяю структуру...');
  const info = await targetPage.evaluate(() => {
    return {
      reportType: document.querySelector('#report_type')?.value,
      groups: document.querySelectorAll('.analytics-group').length,
      allGroups: document.querySelectorAll('[class*="group"]').length,
      tables: document.querySelectorAll('table').length,
      html: document.body.innerHTML.substring(0, 1500)
    };
  });
  
  console.log('📊 Результат:');
  console.log('   Тип отчета:', info.reportType);
  console.log('   .analytics-group:', info.groups);
  console.log('   [class*="group"]:', info.allGroups);
  console.log('   tables:', info.tables);
  
  if (info.reportType !== 'supply_plan') {
    console.log('\n📝 Переключаю на "Поставка"...');
    await targetPage.select('#report_type', 'supply_plan');
    await targetPage.waitForTimeout(5000);
    
    await targetPage.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\check_02_supply.png', fullPage: true });
    console.log('📸 Скриншот: check_02_supply.png');
  }
  
  // Проверяем структуру после переключения
  console.log('\n🔍 Проверяю структуру после переключения...');
  const info2 = await targetPage.evaluate(() => {
    // Ищем таблицы
    const tables = document.querySelectorAll('table');
    const tableInfo = [];
    tables.forEach((t, i) => {
      const rows = t.querySelectorAll('tr');
      tableInfo.push({
        index: i,
        className: t.className,
        rows: rows.length,
        parent: t.parentElement?.className?.substring(0, 30)
      });
    });
    
    return {
      reportType: document.querySelector('#report_type')?.value,
      groups: document.querySelectorAll('.analytics-group').length,
      analyticsStocksWrap: !!document.querySelector('.analytics-stocks-wrap'),
      tableInfo: tableInfo
    };
  });
  
  console.log('📊 Результат:');
  console.log('   Тип отчета:', info2.reportType);
  console.log('   Группы:', info2.groups);
  console.log('   Есть wrap:', info2.analyticsStocksWrap);
  console.log('   Таблицы:', JSON.stringify(info2.tableInfo, null, 2));
  
  await browser.disconnect();
  console.log('\n✅ Готово!');
}

checkPage().catch(err => {
  console.error('❌ Ошибка:', err.message);
  process.exit(1);
});
