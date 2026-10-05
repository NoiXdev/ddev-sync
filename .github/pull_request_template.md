## Summary

<!-- What does this PR change, and why? -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Profile change (a framework's commands or sanitize rules)
- [ ] Refactor / chore
- [ ] Documentation

## Checklist

- [ ] `uv run pytest` passes, and coverage is still 100 % (the gate allows no `# pragma: no cover`)
- [ ] `uv run ruff check .` and `uv run ruff format --check .` pass
- [ ] `uv run mypy` passes
- [ ] Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
- [ ] History is linear — this branch is rebased on `main`, not merged into it

## If a profile changed

- [ ] Every command was verified against the framework's **real** CLI, not from memory —
      paste the `list`/`help` output that proves it
- [ ] The matrix cell for that variant passes: `uv run pytest -m matrix -k <cell> --no-cov`
- [ ] `profiles/VERIFIED.md`'s row says what the run actually proved, and nothing more

## Related issues

<!-- Closes #123, refs #456 -->
