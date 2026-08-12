import type { NextConfig } from "next";
import path from "path";

const nextConfig: NextConfig = {
  turbopack: {
    root: path.resolve("."),
  },
  // Electron loads UI as http://127.0.0.1:3000 — allow dev HMR / assets from that host
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
