import DocsPage from "@/components/docs/DocsPage";
import { getDocumentation } from "@/components/docs/content";
import { documentationMetadata } from "@/components/docs/metadata";

export async function generateMetadata() {
  const documentation = await getDocumentation("released");
  return documentationMetadata(documentation.pages.find((page) => page.slug === "overview")!, "/docs");
}

export default async function DocumentationEntry() {
  const documentation = await getDocumentation("released");
  const page = documentation.pages.find((entry) => entry.slug === "overview")!;
  return <DocsPage documentation={documentation} page={page} version="released" />;
}
