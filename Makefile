.PHONY: help install check run test clean docker-up docker-down

help:
	@echo "Autonomous B2B Supply Chain & Procurement Agent CLI"
	@echo "==================================================="
	@echo "make install    : Install Python dependencies"
	@echo "make check      : Run preflight checks and health probes"
	@echo "make run        : Launch FastAPI backend and Streamlit dashboard"
	@echo "make test       : Execute full pytest test suite"
	@echo "make docker-up  : Spin up containerized services"
	@echo "make docker-down: Stop containerized services"
	@echo "make clean      : Remove cache files and temporary databases"

install:
	pip install -r requirements.txt

check:
	python run_local.py --check

run:
	python run_local.py

test:
	pytest tests/ -v

docker-up:
	docker-compose up --build -d

docker-down:
	docker-compose down

clean:
	rm -rf __pycache__ .pytest_cache data/*.db
