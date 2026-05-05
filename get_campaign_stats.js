const WebSocket = require('ws');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '53E25B889EF0CBCB10C9454D760209F4';

async function getStats() {
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
                                    campaign: {},
                                    statistics: {},
                                    keywords: [],
                                    competitors: [],
                                    gemData: {}
                                };
                                
                                // Основная информация о кампании
                                const titleEl = document.querySelector('h1, [class*="title"]');
                                data.campaign.title = titleEl?.innerText?.trim();
                                
                                // Статус кампании
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text === 'Активна') data.campaign.status = 'Активна';
                                    if (text === 'Приостановлена') data.campaign.status = 'Приостановлена';
                                    if (text === 'Завершена') data.campaign.status = 'Завершена';
                                    if (text?.includes('ID')) {
                                        const match = text.match(/ID\s*(\d+)/);
                                        if (match) data.campaign.id = match[1];
                                    }
                                    if (text?.includes('CPM')) data.campaign.type = 'CPM';
                                    if (text?.includes('CPC')) data.campaign.type = 'CPC';
                                });
                                
                                // Бюджет
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text?.match(/^\d[\d\s]*\\s*₽$/) && text.length < 20) {
                                        if (!data.campaign.budgets) data.campaign.budgets = [];
                                        data.campaign.budgets.push(text);
                                    }
                                });
                                
                                // Основные метрики статистики
                                const allText = document.body.innerText;
                                
                                // Ищем числа с метриками
                                const metricsMap = {};
                                const lines = allText.split('\\n');
                                
                                lines.forEach((line, i) => {
                                    // Показы
                                    if (line.includes('Показы') || line.match(/^Показы\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.match(/[\d\s]+/)) {
                                            metricsMap.shows = nextLine.trim();
                                        }
                                    }
                                    // Клики
                                    if (line.includes('Клики') || line.match(/^Клики\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.match(/[\d\s]+/)) {
                                            metricsMap.clicks = nextLine.trim();
                                        }
                                    }
                                    // CTR
                                    if (line.includes('CTR') || line.match(/^CTR\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('%')) {
                                            metricsMap.ctr = nextLine.trim();
                                        }
                                    }
                                    // CPM
                                    if (line.includes('CPM') || line.match(/^CPM\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('₽')) {
                                            metricsMap.cpm = nextLine.trim();
                                        }
                                    }
                                    // CPC
                                    if (line.includes('CPC') || line.match(/^CPC\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('₽')) {
                                            metricsMap.cpc = nextLine.trim();
                                        }
                                    }
                                    // Заказы
                                    if (line.includes('Заказы') || line.match(/^Заказы\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.match(/[\d\s]+/)) {
                                            metricsMap.orders = nextLine.trim();
                                        }
                                    }
                                    // CR
                                    if (line.includes('CR') || line.match(/^CR\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('%')) {
                                            metricsMap.cr = nextLine.trim();
                                        }
                                    }
                                    // CPO
                                    if (line.includes('CPO') || line.match(/^CPO\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('₽')) {
                                            metricsMap.cpo = nextLine.trim();
                                        }
                                    }
                                    // Расход
                                    if (line.includes('Расход') || line.match(/^Расход\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('₽')) {
                                            metricsMap.spend = nextLine.trim();
                                        }
                                    }
                                    // Доля затрат
                                    if (line.includes('Доля затрат') || line.match(/^Доля затрат\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('%')) {
                                            metricsMap.costShare = nextLine.trim();
                                        }
                                    }
                                    // Сумма заказов
                                    if (line.includes('Сумма заказов') || line.match(/^Сумма заказов\s*$/)) {
                                        const nextLine = lines[i + 1];
                                        if (nextLine && nextLine.includes('₽')) {
                                            metricsMap.revenue = nextLine.trim();
                                        }
                                    }
                                });
                                
                                data.statistics = metricsMap;
                                
                                // Данные по товару из Джем
                                const gemBlocks = [];
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text?.includes('330432124') || text?.includes('В акции')) {
                                        gemBlocks.push(text);
                                    }
                                });
                                data.gemData.raw = gemBlocks.slice(0, 5);
                                
                                // Позиции по запросам
                                const positions = [];
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text?.match(/\\d+\s*→\s*\\d+/)) {
                                        positions.push(text);
                                    }
                                });
                                data.positions = positions.slice(0, 20);
                                
                                // Ключевые слова с высокой частотой
                                const keywords = [];
                                document.querySelectorAll('*').forEach(el => {
                                    const text = el.innerText?.trim();
                                    if (text && text.match(/ножки|подстолье|стол/i) && text.length < 100) {
                                        keywords.push(text);
                                    }
                                });
                                data.keywords = [...new Set(keywords)].slice(0, 20);
                                
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

getStats();
