const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '1ABE8D45A676236DA78843AEEC492196';

async function getData() {
    const ws = new WebSocket(`ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`);
    
    ws.on('open', () => {
        ws.send(JSON.stringify({
            id: 1,
            method: 'Runtime.evaluate',
            params: {
                expression: `
                    (function() {
                        const text = document.body.innerText;
                        const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                        
                        return {
                            // Все строки содержащие "место" с контекстом
                            positions: (() => {
                                const results = [];
                                lines.forEach((line, i) => {
                                    if (line.match(/\\d+\\s*место/) || line.includes('место /')) {
                                        const context = lines.slice(Math.max(0, i-3), i+4).join(' | ');
                                        results.push(context);
                                    }
                                });
                                return results.slice(0, 15);
                            })(),
                            
                            // Строки с запросами (перед позициями обычно идут запросы)
                            queries: (() => {
                                const results = [];
                                lines.forEach((line, i) => {
                                    // Запросы обычно перед "место" и содержат ключевые слова
                                    if ((lines[i+1]?.includes('место') || lines[i+2]?.includes('место')) &&
                                        (line.includes('ножки') || line.includes('стол') || line.includes('подстолье') || line.includes('опора'))) {
                                        results.push(line);
                                    }
                                });
                                return [...new Set(results)].slice(0, 15);
                            })(),
                            
                            // Данные о конкурентах
                            competitors: (() => {
                                const results = [];
                                lines.forEach((line, i) => {
                                    if (line.includes('Дровница') || 
                                        (line.includes('₽') && lines[i-1]?.includes('Дровница')) ||
                                        line.match(/SANBERG|TorgCity|Русский металл/)) {
                                        const context = lines.slice(Math.max(0, i-2), i+3).join(' | ');
                                        results.push(context);
                                    }
                                });
                                return results.slice(0, 10);
                            })(),
                            
                            // Цены конкурентов
                            competitorPrices: (() => {
                                const results = [];
                                lines.forEach(line => {
                                    if (line.match(/\\d[\\d\\s]*\\s*₽/) && 
                                        (line.includes('Дровница') || line.length < 100)) {
                                        results.push(line);
                                    }
                                });
                                return [...new Set(results)].slice(0, 15);
                            })(),
                            
                            // Позиции в поиске (формат "число → число")
                            positionChanges: (() => {
                                const results = [];
                                lines.forEach((line, i) => {
                                    if (line.match(/\\d+\\s*→\\s*\\d+/) || line.match(/>\\d+/)) {
                                        const context = lines.slice(Math.max(0, i-2), i+2).join(' | ');
                                        results.push(context);
                                    }
                                });
                                return results.slice(0, 15);
                            })(),
                            
                            // Время в выдаче
                            timeInSearch: (() => {
                                const results = [];
                                lines.forEach(line => {
                                    if (line.match(/\\d+\\s*ч\\./) || line.match(/\\d+\\s*час/)) {
                                        results.push(line);
                                    }
                                });
                                return [...new Set(results)].slice(0, 10);
                            })(),
                            
                            // Рекламные блоки
                            adBlocks: (() => {
                                const results = [];
                                lines.forEach(line => {
                                    if (line.includes('Реклам') || line.includes('Рекл.')) {
                                        results.push(line);
                                    }
                                });
                                return [...new Set(results)].slice(0, 10);
                            })()
                        };
                    })()
                `,
                returnByValue: true
            }
        }));
    });
    
    ws.on('message', (data) => {
        const msg = JSON.parse(data.toString());
        if (msg.result?.result?.value) {
            console.log(JSON.stringify(msg.result.result.value, null, 2));
        }
    });
    
    ws.on('error', (e) => console.log('Error:', e.message));
    
    setTimeout(() => ws.close(), 5000);
}

getData();
