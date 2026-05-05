const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function getContent() {
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
                                // Получаем весь текст и ищем ключевые данные
                                const text = document.body.innerText;
                                
                                return {
                                    // Информация о товаре
                                    title: document.querySelector('h1')?.innerText?.trim(),
                                    
                                    // Найденные числовые данные
                                    sku: text.match(/Артикул[:\s]*(\d+)/)?.[1],
                                    seller: text.match(/Продавец[:\s]*([^\n]+)/)?.[1]?.trim(),
                                    revenue: text.match(/Выручка[:\s]*([\d\s]+руб)/)?.[1],
                                    stock: text.match(/Остаток[:\s]*(\d+)\s*шт/)?.[1],
                                    salesPerDay: text.match(/Продаж в день[:\s]*([\d,]+)/)?.[1],
                                    revenueDynamic: text.match(/Динамика выручки[:\s]*([^\n]+)/)?.[1],
                                    daysInStock: text.match(/Был в наличии[:\s]*(\d+)\s*дн/)?.[1],
                                    
                                    // Все строки содержащие "место" и контекст
                                    positionLines: text.split('\\n')
                                        .map(l => l.trim())
                                        .filter(l => l.includes('место') || l.includes('позиция'))
                                        .slice(0, 20),
                                    
                                    // Все строки с ценами конкурентов
                                    priceLines: text.split('\\n')
                                        .map(l => l.trim())
                                        .filter(l => l.includes('₽') && (l.includes('Дровница') || l.includes('ножки') || l.includes('подстолье')))
                                        .slice(0, 15),
                                    
                                    // Все строки с данными плагина (содержащие ключевые слова аналитики)
                                    analyticsLines: text.split('\\n')
                                        .map(l => l.trim())
                                        .filter(l => 
                                            l.includes('место') || 
                                            l.includes('выручка') ||
                                            l.includes('продаж') ||
                                            l.includes('остаток') ||
                                            l.includes('конкурент') ||
                                            l.includes('позиция') ||
                                            l.includes('скидка') && l.match(/-\d+%/)
                                        )
                                        .slice(0, 30),
                                    
                                    // Фрагмент текста с ценами
                                    priceContext: text.substring(text.indexOf('₽') - 200, text.indexOf('₽') + 500)
                                };
                            })()
                        `,
                        returnByValue: true
                    }
                }));
            }, 1000);
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

getContent();
