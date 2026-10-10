/**
 * Copy one signed-in Chrome profile into JARVIS's private, ignored profile store.
 * The two Chrome profiles can then be used without taking over daily Chrome.
 */
import fs from "node:fs";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");
const DEST_ROOT = path.join(ROOT, "chrome-profile-data");
const SRC_USER_DATA =
  process.env.CHROME_USER_DATA ||
  path.join(os.homedir(), "Library", "Application Support", "Google", "Chrome");
const SRC_PROFILE = process.env.CHROME_PROFILE_DIRECTORY || "Default";
const SRC_DEFAULT = path.join(SRC_USER_DATA, SRC_PROFILE);
const DEST_PROFILE = process.env.JARVIS_CHROME_PROFILE_DIRECTORY || SRC_PROFILE;
const DEST_PROFILE_PATH = path.join(DEST_ROOT, DEST_PROFILE);

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
  "Extensions",
  "Extension State",
  "Extension Rules",
  "Extension Scripts",
  "History",
  "History-journal",
  "Login Data",
  "Login Data For Account",
  "Web Data",
  "Web Data-journal",
];

function ensureDir(p) {
  fs.mkdirSync(p, { recursive: true });
}

function writeLocalState() {
  const sourcePath = path.join(SRC_USER_DATA, "Local State");
  const localState = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const sourceProfiles = localState.profile?.info_cache || {};
  const profileDirs = fs.readdirSync(DEST_ROOT).filter((entry) => {
    const fullPath = path.join(DEST_ROOT, entry);
    return /^(Default|Profile \d+)$/.test(entry) && fs.statSync(fullPath).isDirectory();
  });
  const infoCache = {};
  for (const profileDir of profileDirs) {
    if (sourceProfiles[profileDir]) infoCache[profileDir] = sourceProfiles[profileDir];
  }
  localState.profile = {
    ...(localState.profile || {}),
    info_cache: infoCache,
    last_used: DEST_PROFILE,
    last_active_profiles: profileDirs,
  };
  fs.writeFileSync(path.join(DEST_ROOT, "Local State"), JSON.stringify(localState));
}

function main() {
  if (!fs.existsSync(SRC_DEFAULT)) {
    console.error("[sync] Source profile missing:", SRC_DEFAULT);
    process.exit(1);
  }
  ensureDir(DEST_ROOT);
  // Avoid copying SQLite databases while Chrome is actively using this profile.
  const openFiles = spawnSync("lsof", ["-n", "-F", "n"], { encoding: "utf8" });
  const inUse = (openFiles.stdout || "").split(/\r?\n/).some(
    (line) => line.startsWith("n") && line.slice(1).startsWith(`${SRC_DEFAULT}${path.sep}`)
  );
  if (inUse) {
    console.error("[sync] This Chrome profile is open. Close its Chrome window, then retry.");
    process.exit(1);
  }
  ensureDir(DEST_PROFILE_PATH);

  console.log("[sync] Source:", SRC_DEFAULT);
  console.log("[sync] Dest:  ", DEST_PROFILE_PATH);

  const exclude = EXCLUDE_DIRS.flatMap((d) => ["--exclude", d]);
  const r = spawnSync(
    "rsync",
    ["-a", "--delete", ...exclude, `${SRC_DEFAULT}/`, `${DEST_PROFILE_PATH}/`],
    { encoding: "utf8", shell: false }
  );
  if ((r.status ?? 1) !== 0) {
    console.error("[sync] rsync failed", r.status, r.stdout, r.stderr);
    process.exit(1);
  }
  writeLocalState();
  try {
    fs.chmodSync(DEST_ROOT, 0o700);
    fs.chmodSync(DEST_PROFILE_PATH, 0o700);
  } catch {
    /* retain current permissions if chmod is unavailable */
  }
  console.log("[sync] Profile synced OK via rsync");
  console.log("[sync] JARVIS user data:", DEST_ROOT);
  console.log("[sync] JARVIS profile:", DEST_PROFILE);
}

main();
