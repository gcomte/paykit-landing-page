# Documentation updates: small, simple releases

## Current direction

Keep the public documentation up to date through small verified releases, using the existing Git branch and Vercel integration. Start from the last verified deployment, not an unapproved pipeline prototype or a dirty working tree.

Read automation/corrections.json before each increment. All earlier user requirements remain a backlog, not simultaneous prerequisites. Agree one useful slice with the user, ship it, then discuss the result and next slice before starting another feature.

The former transaction/semantic-export/review-framework engineering is stopped. Its reports are historical evidence, not a reason to resume work or an implementation to adopt. Earlier elaborate run protocols and conflicting instructions in reference files are not operative for this simplified workflow.

## For each agreed increment

1. State the small scope and what is deferred. Before authoring an overview, run `python automation/check_source.py --version released --page overview` (or `--version coming-soon` for the configured source). On `skip`, stop without model work, generation or a commit. On lookup `error`, report the failure rather than skipping. Use `--force` for an explicit refresh or generation-process change; automatic process-change detection is not part of this first check. A changed upstream commit is a signal for a source-specific update, not permission to relabel stale content.
2. Update the generation inputs or a simple script; do not hand-maintain generated documentation.
3. Check the changed content against the correct public source/version. Keep private Talos context out of public output and public worker inputs.
4. Run relevant checks and the site build. Use a bounded independent review of the changed scope, not a new certification system.
5. Publish only through the already authorized fork branch and existing Vercel integration. Verify the delivered pages.
6. Report what changed, what was actually checked, what remains, and discuss the next increment with the user.

Preserve existing authentication, tools, backend/security settings, unrelated work and the last good deployment. Do not modify protected AGENTS.md, push main/upstream, force-push, or touch the official paykit.to website/domain.

Keep source-change detection, daily 05:00 UTC scheduling, glossary/resources, responsive homepage Docs highlights, every-page Markdown, footer llms.txt, expanded review perspectives and per-run feedback in the requirements backlog. Preserve completed parts; add missing parts one at a time. Do not enable scheduling until a manual update works and scheduling is the agreed next increment. Hands-on SDK/tutorial/playground work remains deferred.
