# Nastrojka bazy dannyh

## Source of truth

Server PostgreSQL is the source of truth for current business data. Local SQLite
or localhost PostgreSQL must be treated only as an explicit development fixture
or restored snapshot. Do not store live `.db`, `.dump`, or `.sql.gz` database
files in git; store schema, migrations, contracts, and small fixtures instead.

Check the active database source before composing real queries:

```bash
python scripts/check_db_source.py
```

Recommended `.env` values:

```env
# Real/current data
DB_SOURCE_MODE=server
ALLOW_LOCAL_DATABASE=false
EXPECTED_DB_HOST=prod-db.example.com

# Local development data
DB_SOURCE_MODE=dev_fixture
ALLOW_LOCAL_DATABASE=true
EXPECTED_DB_HOST=

# Local restored server snapshot
DB_SOURCE_MODE=local_snapshot
ALLOW_LOCAL_DATABASE=true
EXPECTED_DB_HOST=
```

## Bystrostart (SQLite) - GOTOWO

Baza dannyh SQLite uspeshno sozdana i gotova k rabote!

```
Fajl: E:\ozonapi\ozon_analytics.db
Razmer: 208896 bajtov
```

### Dannye dlja podkljuchenija:

Izmenite `.env` fajl:
```env
# SQLite (bystryj start)
DATABASE_URL=sqlite+aiosqlite:///./ozon_analytics.db

# Ostalnye nastrojki...
OZON_CLIENT_ID=your_client_id_here
OZON_API_KEY=your_api_key_here
```

### Proverka raboty:

```bash
# Test podkljuchenija
python -c "import asyncio; from src.database_sqlite import db_manager; asyncio.run(db_manager.initialize()); print('OK:', asyncio.run(db_manager.health_check())); asyncio.run(db_manager.close())"
```

### Zapusk sinhronizacii:

```bash
# Polnaja sinhronizacija
python -m src.main

# Sinhronizacija tovarov
python -m src.main --mode products
```

---

## PostgreSQL (dlja prodakshna)

SQLite horosho dlja:
- Testirovanija
- Malen'kih ob#emov dannyh
- Odinichnogo pol'zovatelja

PostgreSQL rekomenduetsja dlja:
- Bol'shih ob#emov dannyh (> 1 GB)
- Mnogopol'zovatel'skogo dostupa
- Parallel'nyh zaprosov
- Proizvodstvennoj ekspluatacii

### Ustanovka PostgreSQL:

1. **Skachajte ustanovshhik:**
   - https://www.postgresql.org/download/windows/
   - Versija: PostgreSQL 15 ili 16

2. **Pri ustanovke zapomnite:**
   - Port: `5432`
   - Parol' superpol'zovatelja (postgres)

3. **Sozdajte bazu dannyh:**
   - Otkrojte pgAdmin 4 (ustanavlivaetsja vmeste s PostgreSQL)
   - Sozdajte bazu `ozon_analytics`

4. **Obnovite `.env`:**
```env
DATABASE_URL=postgresql+asyncpg://postgres:YOUR_PASSWORD@localhost:5432/ozon_analytics
```

5. **Zapustite inicializaciju:**
```bash
python setup_db.py
```

---

## Struktura bazy dannyh

| Tablica | Opisanie |
|---------|----------|
| `sync_logs` | Logi sinhronizacii |
| `products` | Tovary Ozon |
| `postings` | Otpravlenija (zakazy) |
| `posting_items` | Tovary v otpravlenijah |
| `posting_financials` | Finansovye dannye po otpravlenijam |
| `transactions` | Finansovye operacii |
| `stock_history` | Istorija izmenenija ostakov |
| `campaigns` | Reklamnye kampanii |
| `campaign_statistics` | Statistika kampanij |
| `returns` | Vozvraty tovarov |
| `analytics_data` | Dannye analitiki Ozon |

---

## Reshenie problem

### "Database is locked" (SQLite)
- Zakonchite vse processy, obrashhajushhiesja k baze
- Perezagruzite prilozhenie

### "Connection refused" (PostgreSQL)
- Ubedites', chto PostgreSQL zapuschen
- Prover'te port i parol' v DATABASE_URL

### "Module not found"
```bash
py -m pip install -r requirements.txt
```
