import { createElement as h } from "react";
import { documentationVersions, documentationPath, getDocumentation } from "@/components/docs/content";
import { documentationMarkdown } from "@/components/docs/markdown";

// Index existing published bodies, not the configured source of a future run.
export async function GET() {
  const versions = await Promise.all(documentationVersions.map(async (version) => {
    const documentation = await getDocumentation(version);
    return h("section", { key: version },
      h("h2", null, documentation.version.label),
      h("p", null, h("code", null, documentation.version.commit)),
      h("a", { href: documentation.version.sourceUrl }, "View exact source"),
      h("ul", null, documentation.pages.map((page) => h("li", { key: page.slug },
        h("a", { href: `${documentationPath(version, page.slug)}/markdown` }, page.title)))),
    );
  }));
  return new Response(documentationMarkdown(h("main", null,
    h("h1", null, "Paykit Docs"),
    h("a", { href: "/markdown" }, "Paykit"),
    h("a", { href: "/docs/markdown" }, "Docs"),
    ...versions,
  )), { headers: { "Content-Type": "text/plain; charset=utf-8", "X-Content-Type-Options": "nosniff" } });
}
