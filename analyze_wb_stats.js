const CDP = require('chrome-remote-interface');

const TOKEN = 'codex-browser-relay-dev-token';
const TARGET_ID = '53E25B889EF0CBCB10C9454D760209F4';

async function analyzePage() {
    let client;
    try {
        client = await CDP({
            target: `ws://127.0.0.1:19000/cdp?token=${TOKEN}&targetId=${TARGET_ID}`,
            protocol: 'https://raw.githubusercontent.com/ChromeDevTools/devtools-protocol/master/json/browser_protocol.json'
        });

        const { Runtime, DOM, Page } = client;

        // Ждем загрузки страницы
        await Page.enable();
        await DOM.enable();
        await Runtime.enable();

        // Получаем HTML страницы
        const { result } = await Runtime.evaluate({
            expression: `
                (function() {
                    // Пробуем найти данные в React/Vue состоянии
                    let reactData = null;
                    const root = document.getElementById('root') || document.body;
                    
                    // Ищем данные в window
                    for (let key in window) {
                        if (key.toLowerCase().includes('data') || key.toLowerCase().includes('state') || key.toLowerCase().includes('stats')) {
                            try {
                                const val = window[key];
                                if (val && typeof val === 'object' && JSON.stringify(val).length > 100) {
                                    reactData = { key: key, data: val };
                                    break;
                                }
                            } catch(e) {}
                        }
                    }
                    
                    // Собираем все текстовые данные со страницы
                    const cards = [];
                    document.querySelectorAll('[class*="card"], [class*="stat"], [class*="metric"], [class*="dashboard"]').forEach(el => {
                        const text = el.innerText?.trim();
                        if (text && text.length > 0 && text.length < 500) {
                            cards.push(text);
                        }
                    });
                    
                    // Ищем таблицы с данными
                    const tables = [];
                    document.querySelectorAll('table').forEach(table => {
                        const rows = [];
                        table.querySelectorAll('tr').forEach(tr => {
                            const cells = [];
                            tr.querySelectorAll('td, th').forEach(td => {
                                cells.push(td.innerText?.trim());
                            });
                            if (cells.length > 0) rows.push(cells);
                        });
                        if (rows.length > 0) tables.push(rows);
                    });
                    
                    // Ищем графики/chart данные
                    const charts = [];
                    document.querySelectorAll('[class*="chart"], [class*="graph"], svg').forEach(el => {
                        const ariaLabel = el.getAttribute('aria-label');
                        const title = el.querySelector('title')?.textContent;
                        if (ariaLabel || title) {
                            charts.push(ariaLabel || title);
                        }
                    });
                    
                    // Основные метрики
                    const metrics = [];
                    document.querySelectorAll('span, div, p').forEach(el => {
                        const text = el.innerText?.trim();
                        if (text && (/[\d\s]+[₽%]$/.test(text) || /^[\d\s]+[₽%]/.test(text) || /\d+\.\d+/.test(text))) {
                            if (text.length < 100) metrics.push(text);
                        }
                    });
                    
                    // Заголовки секций
                    const headers = [];
                    document.querySelectorAll('h1, h2, h3, h4, [class*="header"], [class*="title"]').forEach(el => {
                        const text = el.innerText?.trim();
                        if (text && text.length < 200) headers.push(text);
                    });
                    
                    return {
                        url: window.location.href,
                        title: document.title,
                        pageText: document.body.innerText?.substring(0, 5000),
                        cards: cards.slice(0, 50),
                        tables: tables.slice(0, 5),
                        charts: charts.slice(0, 20),
                        metrics: [...new Set(metrics)].slice(0, 50),
                        headers: [...new Set(headers)].slice(0, 30),
                        windowData: reactData ? { key: reactData.key, preview: JSON.stringify(reactData.data).substring(0, 1000) } : null
                    };
                })()
            `,
            returnByValue: true
        });

        console.log(JSON.stringify(result.value, null, 2));

    } catch (err) {
        console.error('Error:', err.message);
    } finally {
        if (client) await client.close();
    }
}

analyzePage();
