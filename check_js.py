import requests

resp = requests.get('http://127.0.0.1:8088/', timeout=10)
html = resp.text

with open('check_result.txt', 'w', encoding='utf-8') as f:
    if 'calculatePalletsForSupplyPlan' in html:
        f.write('OK: Function found\n')
        idx = html.find('async function calculatePalletsForSupplyPlan()')
        if idx > 0:
            f.write('\nCode:\n')
            f.write(html[idx:idx+1200])
    else:
        f.write('NOT FOUND\n')

print('Done')
