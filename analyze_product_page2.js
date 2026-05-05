const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function analyze() {
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
                                return {
                                    url: location.href,
                                    title: document.title,
                                    // Основная инфо
                                    h1: document.querySelector('h1')?.innerText?.trim(),
                                    // Цены
                                    priceElements: [...document.querySelectorAll('*')]
                                        .filter(el => el.innerText?.match(/[\d\s]+\s*₽/))
                                        .map(el => el.innerText?.trim())
                                        .filter(t => t && t.length < 50)
                                        .slice(0, 20),
                                    // Данные плагина - ищем блоки с позициями
                                    pluginInfo: [...document.querySelectorAll('*')]
                                        .filter(el => {
                                            const text = el.innerText || '';
                                            return text.includes('позиция') || 
                                                   text.includes('место') ||
                                                   text.includes('конкурент') ||
                                                   text.includes('средняя цена') ||
                                                   text.includes('минимальная') ||
                                                   text.match(/#\d+/) ||
                                                   text.includes('в корзину');
                                        })
                                        .map(el => el.innerText?.trim())
                                        .filter(t => t && t.length > 5 && t.length < 300)
                                        .slice(0, 40),
                                    // Артикул
                                    sku: document.body.innerText.match(/Арт\.?\s*(\d+)/)?.[1] ||
                                         document.body.innerText.match(/артикул\s*(\d+)/i)?.[1],
                                    // Рейтинг
                                    rating: document.body.innerText.match(/(\d[,\.]\d)\s*⭐/)?.[1] ||
                                            document.body.innerText.match(/(\d[,\.]\d)\s*звезд/)?.[1],
                                    // Количество отзывов
                                    reviews: document.body.innerText.match(/(\d+)\s*оцен/)?.[1] ||
                                             document.body.innerText.match(/(\d+)\s*отзыв/)?.[1]
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

analyze();
