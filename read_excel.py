import pandas as pd
import os

# Находим файл
files = [f for f in os.listdir('.') if f.endswith('.xlsx')]
file = None
for f in files:
    if 'объем' in f.lower() or '��쥬' in f:
        file = f
        break

if not file:
    file = max(files, key=lambda x: os.path.getmtime(x))

print(f'Файл: {file}')
df = pd.read_excel(file)

print('\n=== КОЛОНКИ ===')
for i, col in enumerate(df.columns):
    print(f'{i}: {col}')

print('\n=== ПЕРВЫЕ 10 СТРОК ===')
pd.set_option('display.max_columns', None)
pd.set_option('display.width', 200)
print(df.head(10).to_string())

print('\n=== ИНФО ===')
print(f'Всего строк: {len(df)}')
print(f'\nУникальные значения по колонкам:')
for col in df.columns:
    unique = df[col].nunique()
    print(f'  {col}: {unique} уникальных')
