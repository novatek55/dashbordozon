# Gde skachat' PostgreSQL

## Oficial'nye istochniki

### 1. Oficialnyj sajt (rekomenduetsja)
**URL:** https://www.postgresql.org/download/windows/

**Pryamye ssylki na ustanovshhiki:**
- **PostgreSQL 16.4** (poslednjaja versija): 
  - https://get.enterprisedb.com/postgresql/postgresql-16.4-1-windows-x64.exe
- **PostgreSQL 15.8** (stabilaaja versija):
  - https://get.enterprisedb.com/postgresql/postgresql-15.8-1-windows-x64.exe

### 2. EnterpriseDB (oficialnyj distributiv dlja Windows)
**URL:** https://www.enterprisedb.com/downloads/postgres-postgresql-downloads

## Posagovaja ustanovka

### Shag 1: Skachivanie
1. Perejdite po ssylke: https://www.postgresql.org/download/windows/
2. Najmite "Download the installer"
3. Vyberite versiju (16.4 - novaja, 15.8 - proverennaja)
4. Sohranite fajl (primerno 350 MB)

### Shag 2: Ustanovka
1. Zapustite skachannyj .exe fajl
2. Sledujte masteru ustanovki:
   - **Installation Directory**: ostav'te po umolchaniju (C:\Program Files\PostgreSQL\16)
   - **Data Directory**: ostav'te po umolchaniju
   - **Password**: pridumajte parol' (zapomnite ego!)
   - **Port**: 5432 (po umolchaniju)
   - **Locale**: Russian, Russia
3. Dozhdites' okonchanija ustanovki (5-10 minut)
4. **Ne snimajte galochku** s "Stack Builder" - on nuzhen dlja dopolnenij

### Shag 3: Sozdanie bazy dannyh
1. Otkrojte **pgAdmin 4** (ustanavlivaetsja vmeste s PostgreSQL)
   - Nahoditsja v menju Pusk -> PostgreSQL 16 -> pgAdmin 4
2. Vvedite parol', kotoryj ukazali pri ustanovke
3. V dereve sleva razvernite "Servers" -> "PostgreSQL 16"
4. Pravoj knopkoj na "Databases" -> "Create" -> "Database"
5. V pole "Database" vvedite: `ozon_analytics`
6. Najmite "Save"

### Shag 4: Nastrojka .env
Otkrojte fajl `.env` i izmenite stroku:
```env
# Bylo (SQLite):
DATABASE_URL=sqlite+aiosqlite:///./ozon_analytics.db

# Stalo (PostgreSQL):
DATABASE_URL=postgresql+asyncpg://postgres:VASh_PAROL@localhost:5432/ozon_analytics
```

Zamena:
- `VASh_PAROL` - parol', kotoryj vy ukazali pri ustanovke PostgreSQL
- `postgres` - imja pol'zovatelja (po umolchaniju)

### Shag 5: Inicializacija
```bash
# Ustanovite zavisimost'
py -m pip install asyncpg

# Zapustite inicializaciju
python setup_db.py
```

## Alternativnye sposoby ustanovki

### Cherez Chocolatey (esli ustanovlen)
```powershell
choco install postgresql
```

### Cherez Scoop (esli ustanovlen)
```powershell
scoop install postgresql
```

### Prenosnaja versija (bez ustanovki)
**Postgres Portable:**
- https://github.com/garethflowers/postgres-portable/releases
- Mozhno zapustit' s fleshki, bez ustanovki v sistem

## Proverka ustanovki

Posle ustanovki prover'te, chto vse rabotaet:

```powershell
# Proverka versii
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" --version

# Podkljuchenie k baze (vvedite parol' pri zaprose)
& "C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -d ozon_analytics -c "SELECT version();"
```

## Ezhegodniki (GUI-instrumenty)

Posle ustanovki PostgreSQL stanut dostupny:

1. **pgAdmin 4** - graficheskij klient dlja raboty s bazami
   - Sozdanie baz, tablic, zaprosy
   - Nahoditsja v: Menju Pusk -> PostgreSQL 16 -> pgAdmin 4

2. **SQL Shell (psql)** - komandnaja stroka
   - Nahoditsja v: Menju Pusk -> PostgreSQL 16 -> SQL Shell (psql)

3. **Stack Builder** - menedzher rasshirenij
   - Dlja ustanovki dopolnitel'nyh instrumentov

## Sistemenye trebovanija

- **OS**: Windows 10/11 (64-bit)
- **RAM**: minimum 2 GB (rekomenduetsja 4 GB)
- **Mesto na diske**: 500 MB dlja ustanovki + mesto pod dannye
- **Prava**: Administrator (dlja ustanovki sluzhby)

## Reshenie problem

### "Installer integrity check failed"
- Perezagruzite ustanovshhik
- Prover'te fajl antivirusom

### "Failed to load SQL modules"
- Zapustite ustanovshhik ot imeni administratora
- Otkljuchite antivirus na vremja ustanovki

### Port 5432 zanjat
- Izmenite port na 5433 v processe ustanovki
- Ili osvobodite port: `netstat -ano | findstr 5432`
