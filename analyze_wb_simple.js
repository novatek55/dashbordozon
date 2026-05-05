const http = require('http');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '53E25B889EF0CBCB10C9454D760209F4';

// Получаем страницу через CDP HTTP API
function getPageData() {
    return new Promise((resolve, reject) => {
        const options = {
            hostname: '127.0.0.1',
            port: 19000,
            path: `/json/list?token=${TOKEN}`,
            method: 'GET'
        };

        const req = http.request(options, (res) => {
            let data = '';
            res.on('data', chunk => data += chunk);
            res.on('end', () => {
                try {
                    const targets = JSON.parse(data);
                    const target = targets.find(t => t.id === TARGET_ID);
                    resolve(target);
                } catch (e) {
                    reject(e);
                }
            });
        });

        req.on('error', reject);
        req.end();
    });
}

// Подключаемся к WebSocket и выполняем скрипт
async function analyzeViaWebSocket() {
    const WebSocket = require('ws');
    
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    return new Promise((resolve, reject) => {
        const results = [];
        
        ws.on('open', () => {
            console.log('Connected to CDP');
            
            // Включаем Runtime
            ws.send(JSON.stringify({
                id: 1,
                method: 'Runtime.enable'
            }));
            
            // Выполняем скрипт для получения данных
            setTimeout(() => {
                ws.send(JSON.stringify({
                    id: 2,
                    method: 'Runtime.evaluate',
                    params: {
                        expression: `
                            (function() {
                                // Ищем все числовые значения с рублями/процентами
                                const metrics = [];
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text && (text.includes('₽') || text.includes('%') || /^[\d\s,]+\.?\d*$/.test(text.replace(/\s/g, '')))) {
                                        const parent = el.parentElement;
                                        const label = parent?.innerText?.replace(text, '').trim() || 
                                                     el.previousElementSibling?.innerText?.trim() ||
                                                     el.nextElementSibling?.innerText?.trim();
                                        if (text.length < 50) {
                                            metrics.push({ value: text, context: label?.substring(0, 100) });
                                        }
                                    }
                                });
                                
                                // Ищем заголовки и секции
                                const sections = [];
                                document.querySelectorAll('h1, h2, h3, h4, [class*="title"], [class*="header"]').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text && text.length < 100) sections.push(text);
                                });
                                
                                // Популярные метрики WB
                                const wbMetrics = {};
                                const allText = document.body.innerText;
                                
                                // Ищем ключевые слова
                                const keywords = ['продажи', 'заказы', 'выручка', 'конверсия', 'ctr', 'дрр', 'показы', 'клики', 'списания', 'логистика', 'хранение'];
                                keywords.forEach(kw => {
                                    const regex = new RegExp(kw + '\\s*[:\\-]?\\s*([\\d₽%\\s.,]+)', 'i');
                                    const match = allText.match(regex);
                                    if (match) wbMetrics[kw] = match[1];
                                });
                                
                                return {
                                    url: location.href,
                                    title: document.title,
                                    metrics: metrics.slice(0, 100),
                                    sections: [...new Set(sections)],
                                    wbMetrics: wbMetrics,
                                    pagePreview: allText.substring(0, 3000)
                                };
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
                resolve(msg.result);
            }
        });
        
        ws.on('error', reject);
        ws.on('close', () => resolve());
    });
}

analyzeViaWebSocket().catch(console.error);
