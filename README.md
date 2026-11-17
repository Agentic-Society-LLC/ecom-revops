# Repository template

Create every new repository under `rbediner` or `alliance-properties` from this template (GitHub: "Use this template", or
`gh repo create <owner>/<name> --private --template rbediner/repo-template`). It carries the CI/CD standard so Roman's cost rules
bind from the first commit and any agent that opens the repository finds them.

After creating a repository:

1. In `.github/ci-standard/config.json` set `default_branch` (and `release_branches` if a branch deploys), then regenerate
   `.github/workflows/ci-standard.yml` branches to match (`push: branches:`).
2. Replace this README. Keep `CI-STANDARD.md`, `AGENTS.md`, `CLAUDE.md` and `.github/ci-standard/`.
3. The daily installer in `rbediner/automation-estate` opens a pull request if anything is missing, and the ruleset requiring the
   `ci-standard` check is activated with `python3 scripts/ci-standard-install.py activate --repo <owner>/<name>`.

Files here are byte copies from `rbediner/automation-estate` (see `CI-STANDARD.md`). Do not edit them in this repository.
