const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '53E25B889EF0CBCB10C9454D760209F4';

async function getData() {
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    return new Promise((resolve, reject) => {
        ws.on('open', () => {
            ws.send(JSON.stringify({ id: 1, method: 'Runtime.enable' }));
            
            setTimeout(() => {
                ws.send(JSON.stringify({
                    id: 2,
                    method: 'Runtime.evaluate',
                    params: {
                        expression: `
                            (function() {
                                const data = {
                                    // Основная информация
                                    url: location.href,
                                    title: document.title,
                                    
                                    // Таблица товаров
                                    products: [],
                                    
                                    // Общая информация о поставке
                                    supplyInfo: {},
                                    
                                    // Доступные действия
                                    actions: [],
                                    
                                    // Меню навигации
                                    menu: []
                                };
                                
                                // Извлекаем данные из таблицы
                                const rows = document.querySelectorAll('tr, [class*="row"]');
                                rows.forEach(row => {
                                    const cells = row.querySelectorAll('td, [class*="cell"]');
                                    if (cells.length >= 5) {
                                        const product = {};
                                        cells.forEach((cell, idx) => {
                                            const text = cell.innerText?.trim();
                                            if (text) {
                                                if (idx === 0) product.foto = 'есть';
                                                if (idx === 1) product.barcod = text;
                                                if (idx === 2) product.quantity = text;
                                                if (idx === 3) product.category = text;
                                                if (idx === 4) product.sellerSKU = text;
                                                if (idx === 5) product.wbSKU = text;
                                                if (idx === 6) product.brand = text;
                                                if (idx === 7) product.size = text;
                                                if (idx === 8) product.volume = text;
                                                if (idx === 9) product.color = text;
                                            }
                                        });
                                        if (product.barcod) data.products.push(product);
                                    }
                                });
                                
                                // Ищем информацию о поставке
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text?.includes('Добавлено:')) {
                                        data.supplyInfo.added = text.match(/Добавлено:\s*(.+)/)?.[1];
                                    }
                                    if (text?.includes('черновик')) {
                                        data.supplyInfo.draftStatus = text;
                                    }
                                });
                                
                                // Кнопки действий
                                document.querySelectorAll('button, [role="button"]').forEach(btn => {
                                    const text = btn.innerText?.trim();
                                    if (text && text.length < 50) {
                                        data.actions.push(text);
                                    }
                                });
                                
                                // Меню
                                document.querySelectorAll('nav a, [class*="menu"] a, [class*="nav"] a').forEach(a => {
                                    const text = a.innerText?.trim();
                                    if (text) data.menu.push(text);
                                });
                                
                                // Полный текст для контекста
                                data.fullText = document.body.innerText;
                                
                                return data;
                            })()
                        `,
                        returnByValue: true
                    }
                }));
            }, 500);
        });
        
        ws.on('message', (data) => {
            const msg = JSON.parse(data);
            if (msg.id === 2 && msg.result) {
                console.log(JSON.stringify(msg.result.result.value, null, 2));
                ws.close();
                resolve();
            }
        });
        
        ws.on('error', reject);
    });
}

getData();
