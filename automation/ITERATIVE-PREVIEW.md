# Small incremental website preview

Status: `tested_not_approved`. The parent owns independent review, full build, browser checks and Git/Vercel publication. This is a development preview, not accepted automation replacement, normal generation or strict-profile approval. It cannot advance successful inputs or enable scheduling. Old failures, counters, source mapping, review requirements and publication authority remain unchanged.

## Exact first slice

Markdown/actions cover 13 of 14 currently existing public content routes: `/docs` and the six published articles in each of `released` and `coming-soon`. GET `/docs/markdown` and `/docs/<version>/<slug>/markdown` invoke their actual existing page entry and documentation layout. `/llms.txt` indexes only those working Markdown views using published titles and source commits. Both existing page footers link to `/llms.txt`. The homepage Markdown view/action is pending; no nonworking homepage export link is exposed. Glossary/resources pages do not currently exist and are not invented by this increment.

Content and assets are reused byte-for-byte from deployed site commit `9963e6fd6f3de11f66dd887fdaa88efe0abfa52a`, authenticated against its complete 163-body source map before edits. The development body remains rc8 commit `ddd699676ac18ad8c53d8bf8bac4266951b79c30`, not the rc9 selector configured for a future generation. Existing warnings and source labels are preserved, not reinterpreted or corrected. No upstream source fetch, prose authoring, regeneration or SDK work occurs.

The small view adapter is bounded to the existing synchronous documentation component tree; it is not a general React/HTML serializer or arbitrary future-content contract. It keeps existing navigation, warnings, source identity, headings, ordered links, section targets and diagram alternatives. Native Markdown carries prose/lists/images; localized anchor and exact-code HTML preserves targets and code strings where Markdown fences would change terminal newlines. Consumers that strip raw HTML can lose these boundaries. Focused parity uses the already locked `marked` consumer, React SSR and an independent Python HTML parser. It does not establish browser or all-consumer parity.

## Remaining checks and metadata impact

The parent must use matching physical dependencies, independently inspect frozen bytes/delta, build and browser-test the actual preview, including narrow mobile/footer layout, endpoint HTTP status/content types and exact-SHA deployment readback. The author runs no full suite, production build, browser, server, Cargo or SDK execution. SDK, four guide walkthroughs and playground checks are `not_run_deferred`, nonblocking and not claimed as executed.

`automation/corrections.json` adds only the `iterative-small-public-preview-first` requirement in the existing schema. All prior entries and fields remain intact. This changes correction metadata (and therefore a future generation-process fingerprint); it does not change approval, generation, helper or policy mechanisms, historical records or successful-input checkpoints. `tests/iterative-preview-policy.test.mjs`, `tests/docs-markdown.test.mjs` and its additive parser support are new. Existing tests/helpers/assertions and the lockfile remain unchanged. The operator note records delivery scope, not an acceptance grant.
