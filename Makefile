PY=python3
run:
	PYTHONPATH=src $(PY) -m serptrack.cli serp

legacy:
	PYTHONPATH=src $(PY) -m serptrack.cli legacy

test:
	@if $(PY) -c "import pytest" 2>/dev/null; then \
		PYTHONPATH=src $(PY) -m pytest -q; \
	else \
		echo "pytest 不可用，使用零依赖后备运行器 mini_pytest.py"; \
		PYTHONPATH=src $(PY) mini_pytest.py; \
	fi

clean:
	rm -rf .pytest_cache **/__pycache__ *.db *.db-wal *.db-shm
