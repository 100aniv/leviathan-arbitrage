/** @type {import('next').NextConfig} */
const nextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_ENGINE_URL: process.env.NEXT_PUBLIC_ENGINE_URL || "http://localhost:8000",
    NEXT_PUBLIC_WS_URL:     process.env.NEXT_PUBLIC_WS_URL     || "ws://localhost:8000",
  },
};

module.exports = nextConfig;
