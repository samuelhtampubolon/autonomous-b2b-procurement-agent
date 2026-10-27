.PHONY: help install check run test security clean docker-up docker-down

help:
	@echo "Autonomous B2B Supply Chain & Procurement Agent CLI"
	@echo "==================================================="
	@echo "make install    : Install runtime + dev dependencies"
	@echo "make check      : Run preflight checks, health probes and audit-chain verification"
	@echo "make run        : Launch FastAPI backend and Streamlit dashboard"
	@echo "make test       : Execute full pytest test suite"
	@echo "make security   : Static analysis (bandit) and dependency CVE audit (pip-audit)"
	@echo "make docker-up  : Spin up hardened containerized services (needs SECRET_KEY)"
	@echo "make docker-down: Stop containerized services"
	@echo "make clean      : Remove cache files and temporary databases"

install:
	pip install -r requirements-dev.txt

check:
	python run_local.py --check

run:
	python run_local.py

test:
	pytest tests/ -v

security:
	bandit -c pyproject.toml -r src run_local.py
	pip-audit -r requirements.txt

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down

clean:
	rm -rf __pycache__ .pytest_cache data/*.db
