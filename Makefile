.PHONY: lint test check install-hooks

lint:
	uv tool run ruff check .

test:
	.venv/bin/python -m pytest tests/ -v

check: lint test

install-hooks:
	@echo "Installing pre-commit hook..."
	@cp hooks/pre-commit .git/hooks/pre-commit
	@chmod +x .git/hooks/pre-commit
	@echo "Done."
