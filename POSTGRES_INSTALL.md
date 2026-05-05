# Установка PostgreSQL

## Windows

### Способ 1: Установщик PostgreSQL (рекомендуется)

1. Скачайте установщик с [postgresql.org/download/windows](https://www.postgresql.org/download/windows/)
2. Запустите установщик и следуйте инструкциям:
   - Порт: `5432` (по умолчанию)
   - Запомните пароль суперпользователя (postgres)
3. После установки запустится pgAdmin 4

### Способ 2: Chocolatey

```powershell
# Установите Chocolatey, если еще не установлен
# Затем выполните:
choco install postgresql
```

### Способ 3: Docker (если установлен Docker Desktop)

```powershell
docker run --name ozon_postgres -e POSTGRES_USER=ozon_user -e POSTGRES_PASSWORD=ozon_password -e POSTGRES_DB=ozon_analytics -p 5432:5432 -d postgres:15-alpine
```

## Создание базы данных

После установки PostgreSQL:

### Через psql (командная строка):

```powershell
# Подключитесь к PostgreSQL
psql -U postgres

# Введите пароль суперпользователя

# Создайте базу данных
CREATE DATABASE ozon_analytics;

# Создайте пользователя (опционально)
CREATE USER ozon_user WITH PASSWORD 'ozon_password';

# Дайте права
GRANT ALL PRIVILEGES ON DATABASE ozon_analytics TO ozon_user;

# Выйдите
\q
```

### Через pgAdmin 4:

1. Откройте pgAdmin 4
2. Подключитесь к серверу (правой кнопкой на "PostgreSQL" → "Connect")
3. Правой кнопкой на "Databases" → "Create" → "Database"
4. Введите имя: `ozon_analytics`
5. Нажмите "Save"

## Настройка .env

После создания базы данных обновите файл `.env`:

```env
# Стандартная локальная установка PostgreSQL
DATABASE_URL=postgresql+asyncpg://postgres:your_password@localhost:5432/ozon_analytics

# Или если создали отдельного пользователя:
DATABASE_URL=postgresql+asyncpg://ozon_user:ozon_password@localhost:5432/ozon_analytics
```

## Инициализация базы данных

После настройки PostgreSQL и .env:

```bash
# Установите зависимости
pip install -r requirements.txt

# Запустите скрипт инициализации
python setup_db.py
```

## Проверка подключения

```bash
# Тест подключения
python -c "import asyncio; from src.database import db_manager; from src.config import settings; asyncio.run(db_manager.initialize()); print('✅ OK' if asyncio.run(db_manager.health_check()) else '❌ Failed'); asyncio.run(db_manager.close())"
```

## Решение проблем

### Ошибка: "connection refused"
- Убедитесь, что PostgreSQL запущен
- Проверьте порт (обычно 5432)

### Ошибка: "database does not exist"
- Создайте базу данных через pgAdmin или psql

### Ошибка: "password authentication failed"
- Проверьте пароль в DATABASE_URL
- Убедитесь, что пользователь создан
