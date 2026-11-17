import "server-only";
import corrections from "../../automation/corrections.json";
import { defaultDocumentationVersion, documentationPath, getDocumentation } from "./content";
import styles from "./DocsTeaser.module.css";

function Arrow() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true">
      <path d="M4 12h15m-6-6 6 6-6 6" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// Resolve the selection when the server module loads, including during build.
// An invalid policy must stop the build, not render an undefined card later.
const policy = corrections.landingDocs;
if (policy.version !== "default" || policy.featuredSlugs.length !== 3 || new Set(policy.featuredSlugs).size !== 3) {
  throw new Error("Homepage Docs requires three distinct selected documentation pages.");
}
const version = defaultDocumentationVersion;
const documentation = await getDocumentation(version);
const featuredPages = policy.featuredSlugs.map((slug) => {
  const page = documentation.pages.find((candidate) => candidate.slug === slug);
  if (!page) throw new Error(`Homepage Docs: selected documentation page not found: ${slug}`);
  return page;
});

export default function DocsTeaser() {
  return (
    <section id={policy.sectionId} className={`${styles.section} section-pad`} aria-labelledby="docs-teaser-title">
      <div className={styles.intro}>
        <div>
          <span className="eyebrow">DOCUMENTATION</span>
          <h2 id="docs-teaser-title">Docs for your <span className="title-accent">next step.</span></h2>
          <p className={styles.description}>Understand the payment flow, then explore the local integration tutorials.</p>
        </div>
        <a className={`button button-orange ${styles.mainLink}`} href={policy.mainHref}>Browse the Docs <Arrow /></a>
      </div>
      <ul className={styles.pages} aria-label="Featured documentation">
        {featuredPages.map((page) => (
          <li key={page.slug}>
            <a className={styles.card} href={documentationPath(version, page.slug)}>
              <span className={styles.group}>{page.group}</span>
              <h3>{page.title}</h3>
              <p>{page.description}</p>
              <span className={styles.read}>Read {page.kind === "tutorial" ? "the tutorial" : "the explanation"}<Arrow /></span>
            </a>
          </li>
        ))}
      </ul>
    </section>
  );
}
