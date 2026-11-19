import DocumentationEntry from "../page";
import DocumentationLayout from "../layout";
import { markdownResponse } from "@/components/docs/markdown";

export async function GET() {
  return markdownResponse(DocumentationLayout({ children: await DocumentationEntry() }));
}
