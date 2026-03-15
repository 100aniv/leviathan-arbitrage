/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_ENGINE_URL: process.env.NEXT_PUBLIC_ENGINE_URL || "http://localhost:8000",
    NEXT_PUBLIC_WS_URL:     process.env.NEXT_PUBLIC_WS_URL     || "ws://localhost:8000",
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          {
            key: "Content-Security-Policy",
            value: "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ws://localhost:* wss://localhost:* http://localhost:8000 https://localhost:8000; img-src 'self' data:; font-src 'self'; frame-ancestors 'none';",
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
