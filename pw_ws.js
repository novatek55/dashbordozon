const { chromium } = require('playwright-core');

const WS_URL = 'ws://127.0.0.1:18792/cdp?token=codex-browser-relay-dev-token';

(async () => {
  console.log('🔌 Подключаюсь через WebSocket...');
  
  const browser = await chromium.connect(WS_URL);
  
  console.log('✅ Подключено!');
  
  const contexts = await browser.contexts();
  console.log('Контекстов:', contexts.length);
  
  const context = contexts[0];
  const pages = context.pages();
  
  console.log('Страниц:', pages.length);
  
  for (let i = 0; i < pages.length; i++) {
    console.log(`  [${i}] ${await pages[i].title()}: ${pages[i].url()}`);
  }
  
  // Берем первую страницу с дашбордом
  let page = pages.find(p => p.url().includes('8088'));
  
  if (!page) {
    console.log('❌ Нет страницы с 8088');
    await browser.close();
    return;
  }
  
  console.log('\n📱 Работаем со страницей:', page.url());
  
  // Логи
  page.on('console', msg => console.log('📝', msg.text()));
  
  // Скриншот
  await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\ws_01.png', fullPage: true });
  console.log('📸 ws_01.png');
  
  // Проверяем отчет
  const reportType = await page.evaluate(() => document.querySelector('#report_type')?.value);
  console.log('Текущий отчет:', reportType);
  
  if (reportType !== 'supply_plan') {
    console.log('Переключаю на Поставку...');
    await page.selectOption('#report_type', 'supply_plan');
    await page.waitForTimeout(5000);
    
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\ws_02.png', fullPage: true });
    console.log('📸 ws_02.png');
  }
  
  // Проверяем структуру
  const info = await page.evaluate(() => {
    return {
      groups: document.querySelectorAll('.analytics-group').length,
      groupsAlt: document.querySelectorAll('[class*="group"]').length,
      tables: document.querySelectorAll('table').length,
      firstTable: document.querySelector('table')?.className
    };
  });
  
  console.log('\n📊 Структура:', info);
  
  // Кликаем кнопку
  const hasBtn = await page.evaluate(() => !!document.querySelector('[onclick*="calculatePallets"]'));
  console.log('\n🔘 Кнопка найдена:', hasBtn);
  
  if (hasBtn) {
    console.log('Нажимаю...');
    await page.click('[onclick*="calculatePallets"]');
    await page.waitForTimeout(3000);
    
    await page.screenshot({ path: 'E:\\скрипты OZ\\ozonapi\\ws_03.png', fullPage: true });
    console.log('📸 ws_03.png');
  }
  
  await browser.close();
  console.log('\n✅ Готово!');
})();
