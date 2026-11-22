import { Children, createElement as h, isValidElement, type ReactNode } from "react";

import Image from "next/image";
import { InquiryForm, Teaser } from "../Interactions";
import PaymentStoryController from "../PaymentStoryController";
import { inquiryDraftStatus, teaserVideo } from "../interactionContent";

const escape = (value: string) => value.replace(/&/g, "&amp;").replace(/[\\`*_[\]<>~#!|]/g, "\\$&").replace(/@/g, "&#64;");
const attribute = (value: string) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/\"/g, "&quot;");
const blocks = new Set(["div", "header", "nav", "aside", "main", "article", "section", "footer", "figure", "figcaption", "p", "label", "option", "details", "summary", "form"]);

// The synchronous server homepage is the source. Client references are explicit
// boundaries: never call their functions (or hooks) outside React.
function project(node: ReactNode): ReactNode {
  if (node == null || typeof node === "boolean" || typeof node === "string" || typeof node === "number") return node;
  if (Array.isArray(node)) return Children.map(node, project);
  if (!isValidElement<Record<string, any>>(node)) throw new Error("Unsupported homepage node.");
  const p = node.props;
  if (node.type === PaymentStoryController) return h("figure", { "data-testid": "payment-story" },
    project(p.children),
    p.steps.map((step: { label: ReactNode; title: ReactNode; description: ReactNode }, index: number) => h("section", { key: index },
      h("p", null, project(step.label)), h("h3", null, project(step.title)), h("p", null, project(step.description)))),
    project(p.footer));
  if (node.type === Teaser) return h("div", null, project(p.children),
    h("p", null, h("a", { href: teaserVideo.src }, teaserVideo.playLabel)));
  if (node.type === InquiryForm) return h("div", null, project(p.children), h("p", null, inquiryDraftStatus(p.contactEmail)));
  if (node.type === Image) return h("img", { src: p.src, alt: p.alt });
  if (typeof node.type === "function") return project((node.type as (props: any) => ReactNode)(p));
  if (typeof node.type === "symbol") return project(p.children);
  if (typeof node.type !== "string") throw new Error("Unsupported homepage component.");
  if (p["aria-hidden"] === true || p["aria-hidden"] === "true" || p.hidden === true) return null;
  if (node.type === "input" || node.type === "textarea") return p.placeholder ? h("span", null, " ", p.placeholder) : null;
  return h(node.type, { ...p, children: undefined }, project(p.children));
}

function view(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return escape(String(node));
  if (Array.isArray(node)) return node.map(view).join("");
  if (!isValidElement<Record<string, any>>(node) || typeof node.type !== "string") throw new Error("Unprojected homepage node.");
  const tag = node.type, p = node.props;
  const anchor = p.id ? `\n\n<a id="${attribute(p.id)}"></a>\n\n` : "";
  if (tag === "img") return p.alt ? `![${escape(p.alt)}](<${attribute(p.src)}>)` : "";
  if (tag === "br") return " ";
  if (tag === "a") {
    // Linked cards contain blocks. Keep this small HTML carrier rather than
    // nesting headings/paragraphs in an invalid multiline Markdown link.
    if (hasBlock(p.children)) return anchor + `\n\n<div>${cardHtml(node)}</div>\n\n`;
    return anchor + `[${view(p.children)}](<${attribute(p.href)}>)`;
  }
  const inner = view(p.children);
  if (/^h[1-6]$/.test(tag)) return anchor + `\n\n${"#".repeat(Number(tag[1]))} ${inner}\n\n`;
  if (tag === "em") return anchor + `*${inner}*`;
  if (tag === "strong" || tag === "b") return anchor + `**${inner}**`;
  if (tag === "span" || tag === "html" || tag === "body" || tag === "select" || tag === "button") return anchor + inner;
  if (blocks.has(tag)) return anchor + `\n\n${inner}\n\n`;
  if (tag === "ul") return anchor + `\n\n${inner}\n\n`;
  if (tag === "li") return anchor + `- ${inner.trim().replace(/\n/g, "\n  ")}\n`;
  throw new Error(`Unsupported homepage element: ${tag}`);
}
function hasBlock(node: ReactNode): boolean {
  if (Array.isArray(node)) return node.some(hasBlock);
  return isValidElement<Record<string, any>>(node) && (blocks.has(String(node.type)) || /^h[1-6]$/.test(String(node.type)) || hasBlock(node.props.children));
}

// Only already projected native linked-card descendants reach this carrier.
// No React renderer, client execution, scripts or event attributes are needed.
function cardHtml(node: ReactNode): string {
  if (node == null || typeof node === "boolean") return "";
  if (typeof node === "string" || typeof node === "number") return attribute(String(node));
  if (Array.isArray(node)) return node.map(cardHtml).join("");
  if (!isValidElement<Record<string, any>>(node) || typeof node.type !== "string") throw new Error("Unprojected homepage card.");
  const tag = node.type;
  if (!blocks.has(tag) && !["a", "span", "em", "strong", "b", "img", "br"].includes(tag) && !/^h[1-6]$/.test(tag)) throw new Error(`Unsupported homepage card element: ${tag}`);
  const attributes = ["id", "href", "src", "alt"].filter((key) => typeof node.props[key] === "string")
    .map((key) => ` ${key}="${attribute(node.props[key])}"`).join("");
  return `<${tag}${attributes}>` + (["img", "br"].includes(tag) ? "" : cardHtml(node.props.children) + `</${tag}>`);
}

export function homepageMarkdown(node: ReactNode): string {
  return view(project(node)).replace(/\n{3,}/g, "\n\n").trim() + "\n";
}
