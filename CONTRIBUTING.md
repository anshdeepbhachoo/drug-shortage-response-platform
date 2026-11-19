# Contributing to MediFlow

Thank you for helping improve MediFlow. Keep changes small, reviewable, and respectful of teammates' work.

## Branches

| Branch | Role |
|---|---|
| `main` | Latest merged application baseline. Do not push directly. |
| `dev` | Active integration branch when the team is using one. Branch from here and open pull requests into it for feature work. |
| `feat/*` | New features |
| `fix/*` | Bug fixes |
| `docs/*` | Documentation only |
| `chore/*` | Repository maintenance and CI |

Do not delete, rename, or force-push shared branches. Leave unfinished teammate branches alone unless you are coordinating that work with the branch owner.

## Pull requests

1. Create your branch from the latest agreed integration tip (usually `dev`; use `main` only when the team says so).
2. Keep the pull request focused on one concern.
3. Open the pull request against `dev` for normal feature integration, or against `main` when the team is merging a finished integration or docs baseline.
4. Fill in the pull request template.
5. Do not change unrelated application areas.

## Before you open a PR

- Run the checks that apply to your change:
  - Python: `.venv/bin/python -m pytest -q` (or Windows equivalent)
  - Frontend: `cd apps/web && npm test && npm run build`
- Prefer conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`).
- Never commit secrets, `.env` files, API keys, or credentials.
- Do not rewrite shared Git history.

## Review

- Review your own diff before requesting review.
- Confirm existing tests and dependencies were not changed unless the PR is specifically about them.
- Wait for feedback before merging unfinished work owned by someone else.
