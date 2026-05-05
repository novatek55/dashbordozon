const http = require('http');

const TARGET_ID = 'D8589D3B873C2272BD3D741C8EE11D24';
const TOKEN = 'codex-browser-relay-dev-token';

function fetchJSON(path) {
  return new Promise((resolve, reject) => {
    http.get(`http://127.0.0.1:18792${path}?token=${TOKEN}`, (res) => {
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          resolve(JSON.parse(data));
        } catch (e) {
          resolve(data);
        }
      });
    }).on('error', reject);
  });
}

async function main() {
  console.log('🔍 Получаю информацию о страницах...');
  
  const targets = await fetchJSON('/json/list');
  console.log('Найдено страниц:', targets.length);
  
  for (const t of targets) {
    console.log(`  - ${t.title}: ${t.url}`);
  }
  
  // Активируем цель
  console.log('\n📱 Активирую страницу...');
  await fetchJSON(`/json/activate/${TARGET_ID}`);
  
  console.log('✅ Готово!');
  console.log('\nОткройте DevTools для отладки:');
  console.log(`  http://127.0.0.1:18792/devtools/inspector.html?ws=127.0.0.1:18792/cdp?token=${TOKEN}`);
}

main().catch(console.error);
