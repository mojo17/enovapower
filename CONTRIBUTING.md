# Contributing

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e ".[dev]"
make install-hooks
```

## Development workflow

Before every commit, linting and tests must pass. The pre-commit hook enforces this automatically, but you can also run checks manually:

```bash
make check       # run both lint + tests
make lint        # ruff only
make test        # pytest only
```

## Tools

- **Linter**: [ruff](https://docs.astral.sh/ruff/) — configured in `pyproject.toml`
- **Tests**: [pytest](https://docs.pytest.org/) — test files live in `tests/`
