import DocsPage from "@/components/docs/DocsPage";
import { defaultDocumentationVersion, getDocumentation } from "@/components/docs/content";
import { documentationMetadata } from "@/components/docs/metadata";

export async function generateMetadata() {
  const documentation = await getDocumentation(defaultDocumentationVersion);
  return documentationMetadata(documentation.pages.find((page) => page.slug === "overview")!, "/docs");
}

export default async function DocumentationEntry() {
  const documentation = await getDocumentation(defaultDocumentationVersion);
  const page = documentation.pages.find((entry) => entry.slug === "overview")!;
  return <DocsPage documentation={documentation} page={page} version={defaultDocumentationVersion} markdownHref="/docs/markdown" />;
}
