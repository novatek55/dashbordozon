import sqlite3
import pandas as pd
import os

# Удаляем старую базу
if os.path.exists('palletization.db'):
    os.remove('palletization.db')

# Создаём соединение
conn = sqlite3.connect('palletization.db')
cursor = conn.cursor()

cursor.execute('''
    CREATE TABLE products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku TEXT UNIQUE NOT NULL,
        name TEXT,
        layer_height REAL NOT NULL,
        items_per_layer INTEGER NOT NULL,
        weight_per_item REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')

# Читаем Excel
df = pd.read_excel('обьем.xlsx', engine='openpyxl')

imported = 0
for idx, row in df.iterrows():
    try:
        sku = str(row.get('артикул', '')).strip()
        if not sku or sku.lower() == 'nan':
            continue
        
        layer_height = row.get('СКОЛЬКО В ПАЛЛЕТЕ ЗАНИМАЕТ ВЫСОТЫ')
        items_per_layer = row.get('СКОЛЬКО В ОДИН СЛОЙ ЗАХОДИТ')
        weight = row.get('ВЕС 1шт')
        
        if pd.isna(layer_height) or pd.isna(items_per_layer):
            continue
        
        name_parts = sku.split(' ', 1)
        name = name_parts[1] if len(name_parts) > 1 else sku
        
        cursor.execute('''
            INSERT INTO products (sku, name, layer_height, items_per_layer, weight_per_item)
            VALUES (?, ?, ?, ?, ?)
        ''', (sku, name, float(layer_height), int(items_per_layer), float(weight) if not pd.isna(weight) else None))
        imported += 1
        print(f'Imported: {repr(sku)}')
    except Exception as e:
        print(f'Error: {e}')

conn.commit()

# Проверим что сохранилось
cursor.execute('SELECT sku FROM products')
print('\nAll SKUs in database:')
for row in cursor.fetchall():
    print(f'  {repr(row[0])}')

conn.close()
print(f'\nTotal: {imported}')
