import "server-only";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";

export const documentationVersions = ["released", "coming-soon"] as const;
export type DocumentationVersion = (typeof documentationVersions)[number];
export const documentationSlugs = [
  "overview", "components", "flows", "boundaries", "wallets", "point-of-sale",
] as const;
export type DocumentationSlug = (typeof documentationSlugs)[number];
export type DocumentationLink = { label: string; href: string };
export type DocumentationSection = {
  id: string;
  title: string;
  paragraphs?: string[];
  list?: string[];
  code?: { language: string; code: string; caption?: string }[];
  diagram?: { id: string; caption: string; alt: string };
  links?: DocumentationLink[];
  evidence: string[];
};
export type DocumentationPage = {
  slug: DocumentationSlug;
  title: string;
  description: string;
  group: "Understand Paykit" | "Integrate Paykit";
  kind: "explanation" | "tutorial";
  sections: DocumentationSection[];
  sourceLinks: DocumentationLink[];
};
export type Documentation = {
  version: {
    label: string;
    commit: string;
    tag?: string;
    sourceUrl: string;
    packages: Record<string, unknown>;
    readiness: { notice: string; sourceUrl: string };
  };
  pages: DocumentationPage[];
};

export function isDocumentationVersion(value: string): value is DocumentationVersion {
  return documentationVersions.some((version) => version === value);
}

export function isDocumentationSlug(value: string): value is DocumentationSlug {
  return documentationSlugs.some((slug) => slug === value);
}

export function documentationPath(version: DocumentationVersion, slug: DocumentationSlug) {
  return `/docs/${version}/${slug}`;
}

// Content is generated and validated by the documentation pipeline. Read it on
// the server only; never ship the complete source snapshots to the browser.
export const getDocumentation = cache(async (version: DocumentationVersion): Promise<Documentation> => {
  const file = path.join(process.cwd(), "content", "docs", `${version}.json`);
  return JSON.parse(await readFile(file, "utf8")) as Documentation;
});
