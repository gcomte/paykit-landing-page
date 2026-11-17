# Repository instructions

Preserve the existing Next.js landing page and design. Use npm with package-lock.json. Baseline and documentation checks are in automation/README.md.

Documentation output is generated. Do not manually correct content/docs, public/docs-assets, public/docs-examples or public/docs-manifest.json. Improve generation instructions, source mappings, example recipes or evaluation, then regenerate. Read automation/COORDINATOR.md and its five role files before changing the pipeline. Blueprints are generated intermediates; they are not a hand-maintained documentation source of truth.

Scope: Understand Paykit and Integrate Paykit, initially wallets and point-of-sale. No Develop Paykit section, contribution guide, duplicated API reference or maintained Paykit developer skill. Only verified approved public sources may feed public artifacts. Resolve released/development snapshots once per run. Never substitute master or an rc for a missing stable release. Source and page evidence must match exact commits.

Publication: docs-automation branch on the configured origin fork only. No main writes, upstream pushes, force pushes, automatic upstream PRs or merges, new Vercel project or integration, or security/backend changes. Personal deployment settings belong in ignored .automation/deployment.json, never in portable configuration.

Max two temporary workers. Serialize cargo, website builds and browser tests. Fixed acceptance rules, at most two repair rounds and a bounded run lease. Publish only after deterministic tests, independent factual review, four fresh guide walkthroughs and exact-SHA Vercel verification. A model verdict or successful push alone is insufficient. Existing upstream implementation is read-only; do not patch dependencies to make examples pass.
