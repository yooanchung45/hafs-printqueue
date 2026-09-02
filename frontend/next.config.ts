import type { NextConfig } from "next";

const apiOrigin = process.env.API_ORIGIN ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  experimental: {
    // Allow the app's 100MiB file total plus multipart headers and fields.
    proxyClientMaxBodySize: "110mb",
  },
  // Docker Desktop on Windows can miss native bind-mount file events.
  watchOptions: {
    pollIntervalMs: 500,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiOrigin}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
