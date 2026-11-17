# The learning site

This directory builds the site that explains the lab: its concepts, one page per experiment with the commands to run it, the repository's structure, and the repository's own documents. It uses Astro with Starlight and is published as a GitHub Pages project site at `https://<owner>.github.io/rdma-fabric-lab/`, where `<owner>` is the GitHub account that owns the repository. The build reads the owner from `GITHUB_REPOSITORY` in CI and from the `origin` remote locally.

## Build and preview

```bash
make site              # npm ci when the lock changed, unit tests, then the build into .site/dist
make site-dev          # live reload at http://localhost:4321/rdma-fabric-lab/
cd .site && npm run preview   # serve the built .site/dist
```

Node.js 22 or newer is needed. The Pages workflow uses Node 24. Every dependency in `package.json` is an exact version, and `package-lock.json` pins the rest.

## What the build does

| Step | Script | Fails when |
|---|---|---|
| Copy the repository documents | `scripts/sync-docs.mjs` copies `docs/*.md` and `docs/adr/*.md` into `src/content/docs/docs/` and rewrites their links. The runbook, the verification record and the troubleshooting guide lead the sidebar | A document has no level-one heading |
| Generate page data | The same script writes `src/generated/` from `tracks/*/scenarios/*.yaml`, the `##` comments of `Makefile` and `mk/*.mk`, and `versions.lock.yaml` | A spec or the lock does not parse |
| Render | `astro build`, with `starlight-links-validator` | An internal link or anchor leads nowhere |
| Verify | `scripts/verify-dist.mjs` reads the specs and Makefiles again and checks the built HTML | A page lacks a declared check, a make target or a copied document, or a component link leads nowhere |

`src/content/docs/docs/` and `src/generated/` are written at every build and are not tracked. Paths under `.lab/`, its evidence copies in `.lab/evidence/` among them, and `results/` stay code in the copied documents, because those files exist only on the machine that ran the lab.

## Output excerpts

Every command output on the site is an excerpt of a real run, stored in `src/excerpts/<id>.json` and listed in `excerpts.yaml` with its run id, source file and lines. Pages show them with `<Excerpt id="..." />`. Nobody types an excerpt by hand.

```bash
RFL_EVIDENCE_DIR=<checkout>/.lab/evidence npm run excerpts pull    # copy the listed lines
RFL_EVIDENCE_DIR=<checkout>/.lab/evidence npm run excerpts check   # prove the copies still match
```

A source under `tests/fixtures/` is tracked, and `npm test` checks those excerpts everywhere. A source under `.lab/evidence/` exists only on the machine that ran the lab, so `check` skips it elsewhere and says how many it skipped. `pull` replaces local paths, the host name and the user name of the machine with `<repo>`, `~`, `<host>` and `<user>`, and refuses an excerpt that names the local design draft.

## Layout

| Path | Contents |
|---|---|
| `astro.config.mjs` | Starlight, the five sidebar topics, Mermaid, KaTeX, the plugins, `site` and `base` |
| `src/content/docs/` | The pages: `start/`, `concepts/`, `labs/`, `repository/`, `reference/` |
| `src/components/` | `ScenarioChecks`, `MakeTargets`, `LockScopes`, `LabIndex`, `Excerpt`, `Duration`, `RepoFile` |
| `src/assets/diagrams/` | Hand-written SVG figures. Validate each with `xmllint --noout` |
| `src/plugins/remark-base-links.mjs` | Gives page links such as `/labs/` the `/rdma-fabric-lab` base path |
| `scripts/` | The sync, the post-build verification and the excerpt tool, with their logic in `scripts/lib/` |
| `tests/` | `node --test` unit tests of that logic |

## Publishing

The workflow `.github/workflows/pages.yml` builds the site on every pull request and every push to `main`, and deploys it from `main` only. It uses no secret. Every action is pinned by commit.

The site is published at https://ziwon.github.io/rdma-fabric-lab/. Pages takes its build and deployment from GitHub Actions, and the repository variable `PAGES_ENABLED` is `true`, so every push to `main` runs `.github/workflows/pages.yml`: the `build` job proves the site builds, and the `deploy` job publishes `.site/dist`. Pull requests run only the `build` job.

To stop publishing, delete the variable `PAGES_ENABLED` under Settings, Secrets and variables, Actions, Variables. A push to `main` then runs only the `build` job. A fork that wants its own copy enables Pages with GitHub Actions as the source, allows `main` in the `github-pages` environment if it has branch rules, and sets the same variable.
