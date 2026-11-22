import type { NextConfig } from "next";

const csp = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "frame-src https://www.youtube-nocookie.com",
  "frame-ancestors 'none'",
  "form-action 'self'",
  "base-uri 'self'",
  "object-src 'none'",
].join("; ");

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          { key: "Content-Security-Policy", value: csp },
          { key: "Link", value: '</llms.txt>; rel="describedby"' },
        ],
      },
      // Later same-name headers win; retain describedby with each alternate.
      {
        source: "/",
        headers: [{ key: "Link", value: '</index.md>; rel="alternate"; type="text/markdown", </llms.txt>; rel="describedby"' }],
      },
      {
        source: "/docs",
        headers: [{ key: "Link", value: '</docs/index.md>; rel="alternate"; type="text/markdown", </llms.txt>; rel="describedby"' }],
      },
      {
        source: "/docs/:version/:slug",
        headers: [{ key: "Link", value: '</docs/:version/:slug/index.md>; rel="alternate"; type="text/markdown", </llms.txt>; rel="describedby"' }],
      },
    ];
  },
};
export default config;
