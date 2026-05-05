import pandas as pd
import os
import sys

# Перенаправляем вывод в файл с UTF-8
sys.stdout = open('read_excel_output.txt', 'w', encoding='utf-8')

# Находим файл
files = [f for f in os.listdir('.') if f.endswith('.xlsx')]
file = None
for f in files:
    # Ищем файл по времени изменения (самый свежий)
    pass

file = max(files, key=lambda x: os.path.getmtime(x))

print(f'Файл: {file}')
df = pd.read_excel(file)

print('\n=== КОЛОНКИ ===')
for i, col in enumerate(df.columns):
    print(f'{i}: {repr(col)}')

print('\n=== ПЕРВЫЕ 15 СТРОК ===')
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
print(df.head(15).to_string())

print('\n=== ИНФО ===')
print(f'Всего строк: {len(df)}')
print(f'\nУникальные значения по колонкам:')
for col in df.columns:
    unique = df[col].nunique()
    print(f'  {col}: {unique} уникальных')
