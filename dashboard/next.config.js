/** @type {import('next').NextConfig} */
const engineUrl = process.env.NEXT_PUBLIC_ENGINE_URL || "http://localhost:8000";
const wsUrl     = process.env.NEXT_PUBLIC_WS_URL     || "ws://localhost:8000";
// Build a deduplicated connect-src that always includes localhost and the configured engine URL
const engineWsUrl = engineUrl.replace(/^http/, "ws");
const connectSrcParts = [
  "'self'",
  "ws://localhost:*",
  "wss://localhost:*",
  "http://localhost:8000",
  "https://localhost:8000",
];
if (!engineUrl.includes("localhost")) {
  connectSrcParts.push(engineUrl, engineWsUrl);
}
if (wsUrl && !wsUrl.includes("localhost") && !connectSrcParts.includes(wsUrl)) {
  connectSrcParts.push(wsUrl);
}
const connectSrc = connectSrcParts.join(" ");

const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_ENGINE_URL: engineUrl,
    NEXT_PUBLIC_WS_URL:     wsUrl,
  },
  async rewrites() {
    return [
      {
        source: "/engine-api/:path*",
        destination: `${engineUrl}/:path*`,
      },
    ];
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: `default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval'; style-src 'self' 'unsafe-inline'; connect-src ${connectSrc}; img-src 'self' data:; font-src 'self'; frame-ancestors 'none';`,
          },
          {
            key: "X-Content-Type-Options",
            value: "nosniff",
          },
          {
            key: "X-Frame-Options",
            value: "DENY",
          },
        ],
      },
    ];
  },
};

module.exports = nextConfig;
