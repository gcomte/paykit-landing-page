import VersionedDocumentation from "../page";
import DocumentationLayout from "../../../layout";
import { markdownResponse } from "@/components/docs/markdown";

export async function GET(_request: Request, { params }: { params: Promise<{ version: string; slug: string }> }) {
  return markdownResponse(DocumentationLayout({ children: await VersionedDocumentation({ params }) }));
}
