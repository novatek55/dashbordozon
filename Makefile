.PHONY: help install init-db migrate sync-full sync-products sync-stocks sync-postings sync-transactions sync-returns sync-campaigns analytics docker-up docker-down clean

help:
	@echo "Available commands:"
	@echo "  make install          - Install dependencies"
	@echo "  make init-db          - Initialize database"
	@echo "  make migrate          - Run database migrations"
	@echo "  make sync-full        - Full sync of all data"
	@echo "  make sync-products    - Sync products only"
	@echo "  make sync-stocks      - Sync stocks only"
	@echo "  make sync-postings    - Sync postings only"
	@echo "  make sync-transactions - Sync transactions only"
	@echo "  make sync-returns     - Sync returns only"
	@echo "  make sync-campaigns   - Sync campaigns only"
	@echo "  make analytics        - Run analytics example"
	@echo "  make scheduler        - Start scheduler"
	@echo "  make docker-up        - Start with Docker Compose"
	@echo "  make docker-down      - Stop Docker Compose"
	@echo "  make clean            - Clean logs and cache"

install:
	pip install -r requirements.txt

init-db:
	python -c "import asyncio; from src.database import init_database; asyncio.run(init_database())"

migrate:
	alembic upgrade head

sync-full:
	python -m src.main --mode full

sync-products:
	python -m src.main --mode products

sync-stocks:
	python -m src.main --mode stocks

sync-postings:
	python -m src.main --mode postings

sync-transactions:
	python -m src.main --mode transactions

sync-returns:
	python -m src.main --mode returns

sync-campaigns:
	python -m src.main --mode campaigns

analytics:
	python analytics_example.py

scheduler:
	python scheduler.py

docker-up:
	docker-compose up -d

docker-down:
	docker-compose down

clean:
	rm -rf logs/*.log
	rm -rf __pycache__
	rm -rf src/__pycache__
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete
