/**
 * Clone Chrome Default profile (cookies/logins) into a dedicated JARVIS
 * Chrome user-data dir so automation never fights your open Chrome lock.
 *
 * Source: %LOCALAPPDATA%\Google\Chrome\User Data\Default
 * Dest:   browser-automation/chrome-profile-data/Default
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DEST_ROOT = path.join(ROOT, "chrome-profile-data");
const DEST_DEFAULT = path.join(DEST_ROOT, "Default");
const SRC_USER_DATA =
  process.env.CHROME_USER_DATA ||
  path.join(os.homedir(), "AppData", "Local", "Google", "Chrome", "User Data");
const SRC_PROFILE = process.env.CHROME_PROFILE_DIRECTORY || "Default";
const SRC_DEFAULT = path.join(SRC_USER_DATA, SRC_PROFILE);

const EXCLUDE_DIRS = [
  "Cache",
  "Code Cache",
  "GPUCache",
  "Service Worker",
  "DawnCache",
  "GrShaderCache",
  "ShaderCache",
  "optimization_guide_model_store",
  "JumpListIconsMostVisited",
  "JumpListIconsRecentClosed",
  "blob_storage",
  "File System",
];

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeLocalState() {
  // Minimal Local State so Chrome accepts this user-data dir
  const localState = {
    profile: {
      info_cache: {
        Default: {
          name: "JARVIS",
          is_using_default_name: false,
        },
      },
      last_used: "Default",
    },
  };
  fs.writeFileSync(path.join(DEST_ROOT, "Local State"), JSON.stringify(localState));
}

function main() {
  if (!fs.existsSync(SRC_DEFAULT)) {
    console.error("[sync] Source profile missing:", SRC_DEFAULT);
    process.exit(1);
  }
  ensureDir(DEST_ROOT);
  ensureDir(DEST_DEFAULT);
  writeLocalState();

  console.log("[sync] Source:", SRC_DEFAULT);
  console.log("[sync] Dest:  ", DEST_DEFAULT);

  // Use robocopy on Windows for reliable partial copy
  const xd = EXCLUDE_DIRS.flatMap((d) => ["/XD", d]);
  const args = [
    SRC_DEFAULT,
    DEST_DEFAULT,
    "/E",
    "/NFL",
    "/NDL",
    "/NJH",
    "/NJS",
    "/nc",
    "/ns",
    "/np",
    "/R:1",
    "/W:1",
    ...xd,
  ];
  const r = spawnSync("robocopy.exe", args, { encoding: "utf8", shell: false, windowsHide: true });
  // robocopy exit codes 0-7 are success-ish
  const code = r.status ?? 1;
  if (code >= 8) {
    console.error("[sync] robocopy failed", code, r.stdout, r.stderr);
    process.exit(1);
  }
  console.log("[sync] Profile synced OK (code", code + ")");
  console.log("[sync] Use CHROME_USER_DATA=" + DEST_ROOT);
  console.log("[sync] Use CHROME_PROFILE_DIRECTORY=Default");
}

main();
