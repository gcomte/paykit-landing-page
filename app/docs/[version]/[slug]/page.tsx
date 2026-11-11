import { notFound } from "next/navigation";
import DocsPage from "@/components/docs/DocsPage";
import {
  documentationPath,
  documentationSlugs,
  documentationVersions,
  getDocumentation,
  isDocumentationSlug,
  isDocumentationVersion,
} from "@/components/docs/content";
import { documentationMetadata } from "@/components/docs/metadata";

type Props = { params: Promise<{ version: string; slug: string }> };

export const dynamicParams = false;

export function generateStaticParams() {
  return documentationVersions.flatMap((version) =>
    documentationSlugs.map((slug) => ({ version, slug })),
  );
}

async function resolvePage(params: Props["params"]) {
  const { version, slug } = await params;
  if (!isDocumentationVersion(version) || !isDocumentationSlug(slug)) notFound();
  const documentation = await getDocumentation(version);
  const page = documentation.pages.find((entry) => entry.slug === slug);
  if (!page) notFound();
  return { documentation, page, version };
}

export async function generateMetadata({ params }: Props) {
  const { page, version } = await resolvePage(params);
  return documentationMetadata(page, documentationPath(version, page.slug));
}

export default async function VersionedDocumentation({ params }: Props) {
  return <DocsPage {...await resolvePage(params)} />;
}
