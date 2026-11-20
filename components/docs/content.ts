import "server-only";
import { readFile } from "node:fs/promises";
import path from "node:path";
import { cache } from "react";
import generationConfig from "../../docs-generation.json";

export const documentationVersions = ["released", "coming-soon"] as const;
export type DocumentationVersion = (typeof documentationVersions)[number];
// Policy chooses future generation inputs, never the displayed document identity.
const source: { repository: string; selection?: string; kind?: string; ref?: string } = generationConfig.source;
const newestRelease = source.selection === "newest-release" && Object.keys(source).length === 2;
const fixedRef = source.selection === undefined && typeof source.ref === "string" &&
  ["branch", "tag"].includes(source.kind ?? "") &&
  /^[A-Za-z0-9][A-Za-z0-9._/-]*$/.test(source.ref) &&
  !source.ref.startsWith("refs/") && !source.ref.includes("..") &&
  !source.ref.endsWith(".") && !source.ref.split("/").some((part) => !part || part.startsWith(".") || part.endsWith(".lock"));
// Never silently substitute older docs for an invalid source selector.
if (generationConfig.schemaVersion !== 1 || source.repository !== "pubky/paykit-rs" ||
    !(newestRelease || fixedRef) || generationConfig.defaultVersion !== "coming-soon") {
  throw new Error("Invalid docs-generation source selector.");
}
export const defaultDocumentationVersion: DocumentationVersion = generationConfig.defaultVersion;
export const documentationSlugs = [
  "overview", "components", "flows", "boundaries", "wallets", "point-of-sale", "glossary",
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
  group: "Understand Paykit" | "Integrate Paykit" | "Reference";
  kind: "explanation" | "tutorial" | "reference";
  sections: DocumentationSection[];
  sourceLinks: DocumentationLink[];
};
export type Documentation = {
  // UTC day stamped by an explicit documentation generation, never a site build.
  lastUpdated?: string;
  version: {
    label: string;
    commit: string;
    tag?: string;
    ref?: string;
    kind?: "branch" | "tag";
    sourceUrl: string;
    packages: Record<string, unknown>;
    readiness: { notice: string; sourceUrl: string; attribution?: "upstream" | "documentation-policy" };
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
