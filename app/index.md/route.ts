import { documentationGuide } from "@/components/docs/llms";

export async function GET() {
  return new Response(await documentationGuide(), {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "X-Content-Type-Options": "nosniff" },
  });
}
