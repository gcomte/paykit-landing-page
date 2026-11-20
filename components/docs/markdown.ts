import { isValidElement, type ReactNode } from "react";

// A read-only view of the existing synchronous DocsPage tree, not a new content
// model. Deliberately bounded to the native elements used by published docs.
const containers = new Set(["div", "header", "nav", "aside", "main", "article", "section", "footer", "figure", "figcaption"]);
const html = (value: string) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
const prose = (value: string) => value.replace(/&/g, "&amp;").replace(/[\\`*_[\]<>~#!|]/g, "\\$&");
const destination = (value: string) => `<${html(value)}>`;

function literal(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(literal).join("");
  if (isValidElement<{ children?: ReactNode }>(node)) return literal(node.props.children);
  throw new Error("Documentation code must contain literal text.");
}

function code(value: string, block: boolean, language = ""): string {
  if (!block) {
    // The HTML code boundary is localized: Markdown punctuation in inline HTML
    // is still parsed by common consumers, so protect it as character data.
    return `<code>${html(value).replace(/[\\`*_[\]~!]/g, (c) => `&#${c.codePointAt(0)};`)}</code>`;
  }
  // A fenced block invents a final LF for a source without one. Keep that exact
  // boundary in a code-only HTML block instead; never trim or rewrite code.
  if (!value.endsWith("\n") || value.endsWith("\n\n") || value.includes("\r")) return `\n\n<pre><code>${html(value).replace(/\r/g, "&#13;")}</code></pre>\n\n`;
  const runs = value.match(/`+/g) ?? [];
  const fence = "`".repeat(Math.max(3, ...runs.map((run) => run.length + 1)));
  return `\n\n${fence}${language}\n${value}${fence}\n\n`;
}

export function documentationMarkdown(node: ReactNode): string {
  function view(node: ReactNode): string {
    if (node == null || typeof node === "boolean") return "";
    if (typeof node === "string" || typeof node === "number") return prose(String(node));
    if (Array.isArray(node)) return node.map(view).join("");
    if (!isValidElement<Record<string, unknown> & { children?: ReactNode }>(node)) throw new Error("Unsupported documentation node.");
    if (typeof node.type === "function") {
      const props = node.props;
      return view((node.type as (input: Record<string, unknown> & { children?: ReactNode }) => ReactNode)(props));
    }
    if (typeof node.type === "symbol") return view(node.props.children);
    if (typeof node.type !== "string") throw new Error("Unsupported documentation component.");
    const tag = node.type;
    const p = node.props;
    if (p["aria-hidden"] === true || p["aria-hidden"] === "true" || p.hidden === true) return "";
    const anchor = typeof p.id === "string" ? `\n\n<a id="${html(p.id)}"></a>\n\n` : "";
    if (tag === "pre") return anchor + code(literal(p.children), true, String(p["aria-label"] ?? "").split(" code")[0]);
    if (tag === "code") return anchor + code(literal(p.children), false);
    if (tag === "img") return anchor + `\n\n![${prose(String(p.alt ?? ""))}](${destination(String(p.src))})\n\n`;
    const inner = view(p.children);
    if (tag === "a") return anchor + `[${inner}](${destination(String(p.href))})`;
    if (/^h[1-6]$/.test(tag)) return anchor + `\n\n${"#".repeat(Number(tag[1]))} ${inner}\n\n`;
    if (tag === "span" || tag === "time") return anchor + inner;
    if (tag === "strong") return anchor + `**${inner}**`;
    if (tag === "p" || containers.has(tag)) return anchor + `\n\n${inner}\n\n`;
    if (tag === "ul") return anchor + `\n\n<!-- list boundary -->\n\n${inner}\n\n<!-- list boundary -->\n\n`;
    if (tag === "li") return anchor + `- ${inner.trim().replace(/\n/g, "\n  ")}\n`;
    throw new Error(`Unsupported published documentation element: ${tag}`);
  }
  // Do not normalize the assembled stream: doing so would mutate exact code.
  return view(node);
}

export function markdownResponse(node: ReactNode) {
  return new Response(documentationMarkdown(node), {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "X-Content-Type-Options": "nosniff" },
  });
}
