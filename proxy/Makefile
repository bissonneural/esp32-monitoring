VENV := .venv/bin

install:
	python3 -m venv .venv
	$(VENV)/pip install -q -r requirements-dev.txt

format:
	$(VENV)/ruff format .

check:
	$(VENV)/ruff format --check .
	$(VENV)/ruff check .
	$(VENV)/python -m pytest -q

run:
	$(VENV)/functions-framework --target=api --debug

deploy:
	./deploy.sh

.PHONY: install format check run deploy
