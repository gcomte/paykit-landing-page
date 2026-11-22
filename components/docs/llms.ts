import {
  defaultDocumentationVersion, documentationPath, documentationVersions, getDocumentation,
} from "./content";

// Only catalogue metadata, not a second Paykit narrative or homepage projection.
// Encode each original prose character once; generated references are never
// fed into another punctuation pass. Ordinary title colons remain readable.
const text = (value: string) => value.replace(/\s+/g, " ").replace(/[&\\\x60*_[\]<>~#!|:@.]/g, (char, index, source) => {
  const references: Record<string, string> = { "&": "&amp;", "[": "&#91;", "]": "&#93;", "@": "&#64;" };
  if (Object.hasOwn(references, char)) return references[char];
  if (char === ":") return source.startsWith("//", index + 1) ? "&#58;" : char;
  if (char === ".") return source.slice(Math.max(0, index - 3), index).toLowerCase() === "www" ? "&#46;" : char;
  return "\\" + char;
});

export async function documentationGuide(): Promise<string> {
  const versions = [defaultDocumentationVersion,
    ...documentationVersions.filter((version) => version !== defaultDocumentationVersion)];
  const snapshots = await Promise.all(versions.map(async (version) => ({
    version, documentation: await getDocumentation(version),
  })));
  const lines = [
    "# Paykit", "",
    "> Documentation catalogue: payment coordination, components, flows, responsibilities, wallet and point-of-sale guides, glossary and resources.", "",
    `Default documented snapshot: ${text(snapshots[0].documentation.version.label)} (${defaultDocumentationVersion}). Choose the matching snapshot below; these are published documentation identities, not a claim about the newest upstream release or production readiness.`, "",
  ];
  // Interpretation/version/readiness guidance precedes the H2 resource lists.
  for (const { version, documentation } of snapshots) {
    const source = documentation.version;
    lines.push(`${version}: ${text(source.label)}. Source commit: ${text(source.commit)}. ` +
      (documentation.lastUpdated ? `Generated: ${text(documentation.lastUpdated)}. ` : "") +
      `[Exact source](<${source.sourceUrl}>). ` +
      `${source.readiness.attribution === "documentation-policy" ? "Documentation policy" : "Readiness"}: ${text(source.readiness.notice)} ` +
      `[Readiness source](<${source.readiness.sourceUrl}>).`, "");
  }
  lines.push("## Documentation", "",
    `- [Docs entry](/docs/index.md): Overview for the default documented snapshot, ${text(snapshots[0].documentation.version.label)}.`, "");
  for (const { version, documentation } of snapshots) {
    lines.push(`## ${text(documentation.version.label)} (${version})`, "");
    for (const page of documentation.pages) {
      lines.push(`- [${text(page.title)}](${documentationPath(version, page.slug)}/index.md): ${text(page.description)}`);
    }
    lines.push("");
  }
  return lines.join("\n");
}
