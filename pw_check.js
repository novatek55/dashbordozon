const { chromium } = require('playwright-core');

const CDP_URL = 'http://127.0.0.1:18792/';

(async () => {
  console.log('🔌 Подключаюсь к CDP...');
  
  const browser = await chromium.connectOverCDP(CDP_URL);
  
  console.log('✅ Подключено!');
  console.log('Контекстов:', browser.contexts().length);
  
  const context = browser.contexts()[0] || await browser.newContext();
  const pages = context.pages();
  
  console.log('Страниц:', pages.length);
  
  // Ищем или создаем страницу
  let page = pages.find(p => p.url().includes('8088'));
  
  if (!page) {
    console.log('🆕 Создаю страницу...');
    page = await context.newPage();
    await page.goto('http://127.0.0.1:8088/', { waitUntil: 'networkidle' });
  } else {
    console.log('✓ Найдена страница:', page.url());
  }
  
  // Слушаем консоль
  page.on('console', msg => console.log('📝', msg.text()));
  page.on('pageerror', err => console.log('❌', err.message));
  
  // Скриншот
  await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\pw_01.png', fullPage: true });
  console.log('📸 pw_01.png');
  
  // Проверяем структуру
  const info = await page.evaluate(() => ({
    url: location.href,
    reportType: document.querySelector('#report_type')?.value,
    groups: document.querySelectorAll('.analytics-group').length,
    tables: document.querySelectorAll('table').length
  }));
  
  console.log('\n📊 Инфо:', info);
  
  // Переключаем на Поставку
  if (info.reportType !== 'supply_plan') {
    console.log('\n📝 Переключаю на Поставку...');
    await page.selectOption('#report_type', 'supply_plan');
    await page.waitForTimeout(5000);
    
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\pw_02_supply.png', fullPage: true });
    console.log('📸 pw_02_supply.png');
  }
  
  // Проверяем структуру после
  const info2 = await page.evaluate(() => {
    const tables = document.querySelectorAll('table');
    return {
      groups: document.querySelectorAll('.analytics-group').length,
      tables: tables.length,
      firstTableClass: tables[0]?.className,
      firstTableRows: tables[0]?.querySelectorAll('tr').length
    };
  });
  
  console.log('\n📊 После переключения:', info2);
  
  // Ищем кнопку
  const btnInfo = await page.evaluate(() => {
    const btn = document.querySelector('[onclick*="calculatePallets"]');
    return btn ? { found: true, text: btn.innerText?.substring(0, 30) } : { found: false };
  });
  
  console.log('\n🔘 Кнопка:', btnInfo);
  
  if (btnInfo.found) {
    console.log('🖱️ Нажимаю...');
    await page.click('[onclick*="calculatePallets"]');
    await page.waitForTimeout(3000);
    
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\pw_03_clicked.png', fullPage: true });
    console.log('📸 pw_03_clicked.png');
  }
  
  await browser.close();
  console.log('\n✅ Готово!');
})();
