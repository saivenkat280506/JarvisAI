import type { NextConfig } from "next";
import fs from "fs";
import path from "path";

// The Next.js app lives in frontend/ while node_modules/ is hoisted to the repo
// root on existing checkouts (and may sit beside this file on a fresh
// `npm install` from frontend/). Turbopack must be told which directory is the
// workspace root, otherwise it refuses to resolve `next` from frontend/app.
const frontendDir = __dirname;
const repoRoot = path.resolve(frontendDir, "..");
const workspaceRoot = fs.existsSync(path.join(frontendDir, "node_modules", "next"))
  ? frontendDir
  : repoRoot;

const nextConfig: NextConfig = {
  turbopack: {
    root: workspaceRoot,
  },
  // Electron loads UI as http://127.0.0.1:3000 — allow dev HMR / assets from that host
  allowedDevOrigins: ["127.0.0.1", "localhost"],
};

export default nextConfig;
