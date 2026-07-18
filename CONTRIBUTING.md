# Contributing

## Getting Started

1. Fork and clone the repository.
2. Install dependencies:
   ```bash
   uv sync
   ```
3. Install git hooks:
   ```bash
   uv run pre-commit install --hook-type pre-commit --hook-type pre-push
   ```

## Development Workflow

1. Create a focused branch from `main`.
2. Make your changes.
3. Run checks:
   ```bash
   uv run pre-commit run --all-files
   uv run pre-commit run --hook-stage pre-push --all-files
   ```
4. Open a pull request with a concise description.

## Testing

```bash
# Run all tests
uv run pytest

# Run with coverage
uv run pytest --cov --cov-report=term-missing --cov-report=xml
```

Coverage is configured in `pyproject.toml` with an 80% minimum gate.

## Code Style

- **Formatter:** Black (line length 99)
- **Linter:** Flake8
- **Type checker:** mypy (advisory mode — does not block)
- All tools use `@mcp.tool` + `@with_account` + `@validate_id` decorators.
- Error handling: wrap in `try/except`, return `log_and_format_error(...)`.

## Adding a New Tool

1. Add the tool function to the appropriate file in `telegram_mcp/tools/`.
2. Use the decorator stack: `@mcp.tool(annotations=ToolAnnotations(...))` → `@with_account(readonly=...)` → `@validate_id(...)`.
3. Export in `telegram_mcp/tools/__init__.py` via star import.
4. Add tests in `tests/`.

## Commit Messages

Use [Conventional Commits](https://www.conventionalcommits.org/) format:
- `feat(scope): description` for new features
- `fix(scope): description` for bug fixes
- `docs(scope): description` for documentation changes

## License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.
