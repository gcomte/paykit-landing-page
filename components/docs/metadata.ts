import type { Metadata } from "next";
import type { DocumentationPage } from "./content";

export function documentationMetadata(page: DocumentationPage, route: string): Metadata {
  const title = `${page.title} | Paykit Docs`;
  return {
    title,
    description: page.description,
    alternates: { canonical: route },
    robots: { index: false, follow: true },
    openGraph: {
      type: "article",
      siteName: "Paykit",
      title,
      description: page.description,
      url: route,
    },
    twitter: { card: "summary", title, description: page.description },
  };
}
