# Contributing to deepiri-polylogue

## Workflow

This repo uses [polylogue](https://github.com/Team-Deepiri/deepiri-polylogue) for multi-agent coordination.

### Starting work

1. Pull latest: `git pull`
2. Run tests: `make test` (or `pytest`)
3. Check lint: `make lint` (or `ruff check .`)

### While working

- Ensure you're using the latest codebase before each substantive change
- Run tests locally before pushing
- Check lint/type before pushing

### Before pushing

1. `make test && make lint` (or equivalent)
2. `git add . && git commit -m "description"`
3. `git pull --rebase` if needed, then `git push`

### Commit style

- `feat:`, `fix:`, `docs:`, `test:`, `chore:`, `refactor:` prefix
- Keep messages short but descriptive

## Security

Do not put API keys, tokens, or sensitive data in journal events.

## License

Apache 2.0 — see LICENSE file.