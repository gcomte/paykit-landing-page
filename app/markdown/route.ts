import Home from "../page";
import RootLayout from "../layout";
import { homepageMarkdown } from "@/components/homepage/markdown";

// Match the actual homepage request-time conference visibility.
export const dynamic = "force-dynamic";

export function GET() {
  return new Response(homepageMarkdown(RootLayout({ children: Home() })), {
    headers: { "Content-Type": "text/markdown; charset=utf-8", "X-Content-Type-Options": "nosniff" },
  });
}
