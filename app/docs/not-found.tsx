import styles from "@/components/docs/docs.module.css";

export default function DocumentationNotFound() {
  return (
    <main className={styles.notFound}>
      <p className={styles.eyebrow}>PAYKIT DOCS · 404</p>
      <h1>Page not found.</h1>
      <p>This documentation page or version doesn’t exist.</p>
      <a href="/docs">Go to the released documentation <span aria-hidden="true">→</span></a>
    </main>
  );
}
