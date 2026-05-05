const WebSocket = require('ws');

const TARGET_ID = 'D8589D3B873C2272BD3D741C8EE11D24';
const WS_URL = `ws://127.0.0.1:18792/cdp?token=codex-browser-relay-dev-token`;

async function sendCDPCommand(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = Math.floor(Math.random() * 100000);
    const message = JSON.stringify({ id, method, params });
    
    const handler = (data) => {
      try {
        const response = JSON.parse(data);
        if (response.id === id) {
          ws.off('message', handler);
          resolve(response);
        }
      } catch (e) {}
    };
    
    ws.on('message', handler);
    ws.send(message);
    
    setTimeout(() => {
      ws.off('message', handler);
      reject(new Error('Timeout'));
    }, 10000);
  });
}

async function main() {
  console.log('🔌 Подключаюсь к CDP...');
  
  const ws = new WebSocket(WS_URL);
  
  await new Promise((resolve, reject) => {
    ws.on('open', resolve);
    ws.on('error', reject);
  });
  
  console.log('✅ Подключено!');
  
  // Активируем страницу
  console.log('📱 Активирую страницу...');
  await sendCDPCommand(ws, 'Target.activateTarget', { targetId: TARGET_ID });
  
  // Получаем URL
  console.log('🌐 Проверяю URL...');
  const urlResult = await sendCDPCommand(ws, 'Runtime.evaluate', {
    expression: 'window.location.href'
  });
  console.log('URL:', urlResult.result?.value);
  
  // Если не дашборд, переходим
  if (!urlResult.result?.value?.includes('8088')) {
    console.log('🌐 Перехожу на дашборд...');
    await sendCDPCommand(ws, 'Page.navigate', {
      url: 'http://127.0.0.1:8088/'
    });
    await new Promise(r => setTimeout(r, 5000));
  }
  
  // Ждем загрузки
  console.log('⏳ Жду загрузки...');
  await new Promise(r => setTimeout(r, 3000));
  
  // Скриншот
  console.log('📸 Делаю скриншот...');
  const screenshot = await sendCDPCommand(ws, 'Page.captureScreenshot');
  require('fs').writeFileSync('E:\\скрипты OZ\\ozonapi\\cdp_shot_01.png', Buffer.from(screenshot.data, 'base64'));
  console.log('📸 Сохранено: cdp_shot_01.png');
  
  // Проверяем структуру
  console.log('\n🔍 Проверяю структуру...');
  const evalResult = await sendCDPCommand(ws, 'Runtime.evaluate', {
    expression: `JSON.stringify({
      url: window.location.href,
      reportType: document.querySelector('#report_type')?.value,
      groups: document.querySelectorAll('.analytics-group').length,
      allGroups: document.querySelectorAll('[class*="group"]').length,
      tables: document.querySelectorAll('table').length,
      supplyTables: document.querySelectorAll('.supply-detail-table').length,
      analyticsStocksWrap: !!document.querySelector('.analytics-stocks-wrap')
    })`
  });
  
  const info = JSON.parse(evalResult.result?.value || '{}');
  console.log('📊 Структура:', info);
  
  // Если не Поставка, переключаем
  if (info.reportType !== 'supply_plan') {
    console.log('\n📝 Переключаю на "Поставка"...');
    
    // Выбираем значение в селекте
    await sendCDPCommand(ws, 'Runtime.evaluate', {
      expression: `
        const select = document.querySelector('#report_type');
        select.value = 'supply_plan';
        select.dispatchEvent(new Event('change'));
        'switched'
      `
    });
    
    await new Promise(r => setTimeout(r, 5000));
    
    // Скриншот после переключения
    console.log('📸 Делаю скриншот...');
    const screenshot2 = await sendCDPCommand(ws, 'Page.captureScreenshot');
    require('fs').writeFileSync('E:\\скрипты OZ\\ozonapi\\cdp_shot_02_supply.png', Buffer.from(screenshot2.data, 'base64'));
    console.log('📸 Сохранено: cdp_shot_02_supply.png');
  }
  
  // Проверяем структуру еще раз
  console.log('\n🔍 Проверяю структуру после переключения...');
  const evalResult2 = await sendCDPCommand(ws, 'Runtime.evaluate', {
    expression: `JSON.stringify({
      reportType: document.querySelector('#report_type')?.value,
      groups: document.querySelectorAll('.analytics-group').length,
      tables: document.querySelectorAll('table').length,
      firstTableHTML: document.querySelector('table')?.outerHTML?.substring(0, 500)
    })`
  });
  
  const info2 = JSON.parse(evalResult2.result?.value || '{}');
  console.log('📊 Структура:', info2);
  
  // Ищем кнопку паллетизации
  console.log('\n🖱️ Ищу кнопку...');
  const btnResult = await sendCDPCommand(ws, 'Runtime.evaluate', {
    expression: `
      const btn = document.querySelector('[onclick*="calculatePallets"]');
      btn ? JSON.stringify({
        found: true,
        text: btn.innerText?.substring(0, 50),
        onclick: btn.getAttribute('onclick')?.substring(0, 50)
      }) : JSON.stringify({ found: false })
    `
  });
  
  const btnInfo = JSON.parse(btnResult.result?.value || '{}');
  console.log('Кнопка:', btnInfo);
  
  if (btnInfo.found) {
    console.log('🖱️ Нажимаю кнопку...');
    await sendCDPCommand(ws, 'Runtime.evaluate', {
      expression: `
        const btn = document.querySelector('[onclick*="calculatePallets"]');
        if (btn) {
          btn.click();
          'clicked'
        }
      `
    });
    
    await new Promise(r => setTimeout(r, 3000));
    
    console.log('📸 Делаю скриншот после клика...');
    const screenshot3 = await sendCDPCommand(ws, 'Page.captureScreenshot');
    require('fs').writeFileSync('E:\\скрипты OZ\\ozonapi\\cdp_shot_03_clicked.png', Buffer.from(screenshot3.data, 'base64'));
    console.log('📸 Сохранено: cdp_shot_03_clicked.png');
  }
  
  ws.close();
  console.log('\n✅ Готово!');
}

main().catch(err => {
  console.error('❌ Ошибка:', err.message);
  process.exit(1);
});
