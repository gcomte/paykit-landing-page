import type { ReactNode } from "react";
import {
  documentationPath,
  type Documentation,
  type DocumentationPage,
  type DocumentationSection,
  type DocumentationVersion,
} from "./content";
import styles from "./docs.module.css";

function InlineCode({ text }: { text: string }) {
  return text.split(/(`[^`]+`)/g).map((part, index) =>
    part.startsWith("`") && part.endsWith("`")
      ? <code key={index}>{part.slice(1, -1)}</code>
      : <span key={index}>{part}</span>,
  );
}

function Arrow({ external = false }: { external?: boolean }) {
  return <span aria-hidden="true">{external ? "↗" : "→"}</span>;
}

function ExternalLink({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href}>{children} <Arrow external /></a>;
}

function PageNavigation({
  pages, version, current,
}: { pages: DocumentationPage[]; version: DocumentationVersion; current: string }) {
  return (
    <nav aria-label="Documentation pages" className={styles.pageNavigation}>
      {(["Understand Paykit", "Integrate Paykit", "Reference"] as const).filter((group) => pages.some((page) => page.group === group)).map((group) => (
        <div key={group} className={styles.navigationGroup}>
          <h2>{group}</h2>
          <ul>
            {pages.filter((page) => page.group === group).map((page) => (
              <li key={page.slug}>
                <a href={documentationPath(version, page.slug)} aria-current={current === page.slug ? "page" : undefined}>
                  {page.title}
                </a>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );
}

function Section({ section, version }: { section: DocumentationSection; version: DocumentationVersion }) {
  return (
    <section id={section.id} className={styles.section} aria-labelledby={`${section.id}-title`}>
      <h2 id={`${section.id}-title`}>{section.title}</h2>
      {section.paragraphs?.map((paragraph, index) => <p key={index}><InlineCode text={paragraph} /></p>)}
      {section.list && (
        <ul className={styles.contentList}>
          {section.list.map((item, index) => <li key={index}><InlineCode text={item} /></li>)}
        </ul>
      )}
      {section.code?.map((block, index) => (
        <figure key={index} className={styles.codeFigure}>
          <figcaption id={`${section.id}-code-${index}`} className={styles.codeCaption}>
            <span>{block.language}</span>
            {block.caption && <span>{block.caption}</span>}
          </figcaption>
          <pre tabIndex={0} role="region" aria-label={`${block.language} code${block.caption ? `: ${block.caption}` : ""}`}>
            <code>{block.code}</code>
          </pre>
        </figure>
      ))}
      {section.diagram && (
        <figure className={styles.diagram}>
          {/* Static SVGs are generated at build time, not rendered as executable markup. */}
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={`/docs-assets/${version}/${section.diagram.id}.svg`}
            alt={section.diagram.alt}
            loading="lazy"
            decoding="async"
          />
          <figcaption>{section.diagram.caption}</figcaption>
          <p className={styles.diagramAlternative} data-testid="diagram-text-alternative">
            <strong>In words:</strong> <InlineCode text={section.diagram.alt} />
          </p>
        </figure>
      )}
      {section.links && section.links.length > 0 && (
        <ul className={styles.sectionLinks}>
          {section.links.map((link) => (
            <li key={link.href}><ExternalLink href={link.href}>{link.label}</ExternalLink></li>
          ))}
        </ul>
      )}
    </section>
  );
}

export default function DocsPage({
  documentation, page, version, markdownHref,
}: { documentation: Documentation; page: DocumentationPage; version: DocumentationVersion; markdownHref?: string }) {
  const comingSoon = version === "coming-soon";
  const versionName = comingSoon ? "Selected source" : "Released";
  const pageIndex = documentation.pages.findIndex((entry) => entry.slug === page.slug);
  const previous = documentation.pages[pageIndex - 1];
  const next = documentation.pages[pageIndex + 1];
  const coveredVersion = documentation.version.tag ?? documentation.version.ref ?? documentation.version.label;
  const updatedDate = documentation.lastUpdated && new Intl.DateTimeFormat("en-US", {
    month: "short", day: "numeric", year: "numeric", timeZone: "UTC",
  }).format(new Date(`${documentation.lastUpdated}T00:00:00Z`));
  return (
    <div className={styles.shell}>
      <a href="#docs-content" className="skip-link">Skip to documentation</a>
      <header className={styles.header}>
        <div className={styles.headerInner}>
          <a href="/" className={styles.brand} aria-label="Paykit home"><span className="wordmark">Paykit</span></a>
          <a href="/docs" className={styles.docsLabel}>Docs</a>
          <a href="/" className={styles.backHome}>Back to Paykit <Arrow /></a>
        </div>
      </header>
      <div className={styles.layout}>
        <aside className={styles.sidebar}>
          <div className={styles.sidebarInner}>
            <p className={styles.navigationLabel}>DOCUMENTATION</p>
            <PageNavigation pages={documentation.pages} version={version} current={page.slug} />
          </div>
        </aside>
        <main id="docs-content" tabIndex={-1} className={styles.main} data-doc-version={version}>
          <div className={styles.versionRow}>
            <span className={styles.versionLabel}>Documentation version</span>
            <nav aria-label="Documentation version" className={styles.versionSwitcher}>
              <a href={documentationPath("released", page.slug)} aria-current={!comingSoon ? "page" : undefined}>Released</a>
              <a href={documentationPath("coming-soon", page.slug)} aria-current={comingSoon ? "page" : undefined}>Selected source</a>
            </nav>
          </div>
          {comingSoon && <p role="note" aria-label="Selected source version" className={styles.warning}>Selected source snapshot—check its ref and commit before integrating.</p>}
          <aside role="note" aria-label="Software readiness" className={styles.warning} data-testid="readiness-notice">
            <p>{documentation.version.readiness.notice}</p>
            <ExternalLink href={documentation.version.readiness.sourceUrl}>{documentation.version.readiness.attribution === "documentation-policy" ? "Read upstream README" : "Upstream readiness statement"}</ExternalLink>
          </aside>
          <article aria-labelledby="docs-title">
            <div className={styles.articleHeader}>
              <p className={styles.eyebrow}>{page.group}</p>
              <h1 id="docs-title">{page.title}</h1>
              <p className={styles.description} data-testid="docs-description"><InlineCode text={page.description} /></p>
              <p className={styles.kind} data-testid="docs-page-kind">{page.kind === "tutorial" ? "Integration tutorial" : page.kind === "reference" ? "Reference" : "Explanation"}</p>
              <a href={markdownHref ?? `${documentationPath(version, page.slug)}/markdown`} className={styles.markdownAction}>View as Markdown</a>
            </div>
            <aside className={styles.provenance} aria-label="Documentation source" data-testid="docs-version-marker">
              <div className={styles.provenanceHeading}>
                <strong>{versionName}</strong>
                {documentation.version.label !== versionName && documentation.version.label !== documentation.version.tag && <span>{documentation.version.label}</span>}
                {documentation.version.ref
                  ? <span className={styles.tag}>{documentation.version.kind === "tag" ? "Tag" : "Branch"}: {documentation.version.ref}</span>
                  : documentation.version.tag && <span className={styles.tag}>Tag: {documentation.version.tag}</span>}
              </div>
              <p className={styles.commit}><span>Source commit</span> <code>{documentation.version.commit}</code></p>
              <ExternalLink href={documentation.version.sourceUrl}>View exact source</ExternalLink>
            </aside>
            {page.sections.length > 1 && (
              <nav aria-label="On this page" className={styles.contents}>
                <span>On this page</span>
                <ul>{page.sections.map((section) => <li key={section.id}><a href={`#${section.id}`}>{section.title}</a></li>)}</ul>
              </nav>
            )}
            <div className={styles.sections}>
              {page.sections.map((section) => <Section key={section.id} section={section} version={version} />)}
            </div>
          </article>
          <nav aria-label="Next steps" className={styles.nextSteps}>
            <h2>Next steps</h2>
            <div>
              {previous && <a href={documentationPath(version, previous.slug)}><span>Previous</span><strong>{previous.title}</strong><Arrow /></a>}
              {next && <a href={documentationPath(version, next.slug)}><span>Continue reading</span><strong>{next.title}</strong><Arrow /></a>}
            </div>
          </nav>
          <footer className={styles.sources}>
            <p className={styles.footerMetadata} data-testid="docs-footer-metadata">
              Covers Paykit {coveredVersion}
              {updatedDate && <> · Last updated: <time dateTime={documentation.lastUpdated}>{updatedDate}</time></>}
            </p>
            <nav aria-label="Page sources">
              <h2>Source references</h2>
              <p>The upstream material behind this page.</p>
              <ul>{page.sourceLinks.map((link) => <li key={link.href}><ExternalLink href={link.href}>{link.label}</ExternalLink></li>)}</ul>
            </nav>
            <a href="#docs-content" className={styles.backToTop}>Back to top <span aria-hidden="true">↑</span></a>
            <a href="/llms.txt" className={styles.backToTop}>llms.txt</a>
          </footer>
        </main>
      </div>
    </div>
  );
}
