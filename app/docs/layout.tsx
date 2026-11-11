import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Documentation | Paykit",
  description: "Versioned Paykit explanations and integration tutorials.",
  alternates: { canonical: "/docs" },
  robots: { index: false, follow: true },
  openGraph: { title: "Paykit Docs", url: "/docs", description: "Versioned Paykit explanations and integration tutorials." },
  twitter: { card: "summary", title: "Paykit Docs", description: "Versioned Paykit explanations and integration tutorials." },
};

export default function DocumentationLayout({ children }: { children: React.ReactNode }) {
  return children;
}
