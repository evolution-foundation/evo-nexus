/**
 * EvoNexus CLI — plugin subcommand
 *
 * Usage:
 *   npx @evoapi/evo-nexus plugin install <url>
 *   npx @evoapi/evo-nexus plugin list
 *   npx @evoapi/evo-nexus plugin uninstall <slug>
 *   npx @evoapi/evo-nexus plugin update <slug>
 *   npx @evoapi/evo-nexus plugin init [name]
 */

import { execSync } from "child_process";
import { existsSync, mkdirSync, writeFileSync, readdirSync, readFileSync } from "fs";
import { resolve, join } from "path";
import { fileURLToPath } from "url";
import { dirname } from "path";

const GREEN = "\x1b[92m";
const CYAN = "\x1b[96m";
const YELLOW = "\x1b[93m";
const RED = "\x1b[91m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

const __dir = dirname(fileURLToPath(import.meta.url));
const SKELETON_DIR = resolve(__dir, "../../templates/plugin-skeleton");

// ── Resolve API base URL from environment ────────────────────────────────────

function apiBase() {
  return process.env.EVONEXUS_API_URL ?? "http://localhost:8080";
}

async function apiRequest(method, path, body) {
  const token = process.env.DASHBOARD_API_TOKEN ?? "";
  const url = `${apiBase()}/api${path}`;
  const opts = {
    method,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    ...(body ? { body: JSON.stringify(body) } : {}),
  };
  const { default: fetch } = await import("node-fetch").catch(() => {
    // Node 18+ has global fetch; fall back gracefully
    return { default: globalThis.fetch };
  });
  const fn = fetch ?? globalThis.fetch;
  const res = await fn(url, opts);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`API ${method} ${path} → ${res.status}: ${text}`);
  }
  return res.json();
}

// ── Subcommand handlers ───────────────────────────────────────────────────────

async function cmdInstall(args) {
  const url = args[0];
  if (!url) {
    console.error(`  ${RED}Usage: plugin install <https-url>${RESET}\n`);
    process.exit(1);
  }
  console.log(`  ${BOLD}Installing plugin from:${RESET} ${DIM}${url}${RESET}\n`);
  try {
    const data = await apiRequest("POST", "/plugins/install", { source_url: url });
    console.log(`  ${GREEN}✓${RESET} Installed: ${BOLD}${data.slug}${RESET} (status: ${data.status})`);
    if (data.warnings && data.warnings.length > 0) {
      console.log(`\n  ${YELLOW}Warnings:${RESET}`);
      data.warnings.forEach((w) => console.log(`    ${YELLOW}!${RESET} ${w}`));
    }
    if (data.routine_activation_pending) {
      console.log(`\n  ${YELLOW}!${RESET} Restart the dashboard to activate plugin routines.`);
    }
    console.log();
  } catch (e) {
    console.error(`  ${RED}Install failed: ${e.message}${RESET}\n`);
    process.exit(1);
  }
}

async function cmdList() {
  try {
    const plugins = await apiRequest("GET", "/plugins");
    if (!Array.isArray(plugins) || plugins.length === 0) {
      console.log(`  ${DIM}No plugins installed.${RESET}\n`);
      return;
    }
    console.log(`\n  ${BOLD}Installed plugins (${plugins.length}):${RESET}\n`);
    const maxSlug = Math.max(...plugins.map((p) => p.slug.length), 4);
    const maxVer  = Math.max(...plugins.map((p) => p.version.length), 7);
    console.log(
      `  ${"SLUG".padEnd(maxSlug)}  ${"VERSION".padEnd(maxVer)}  STATUS`
    );
    console.log(`  ${"-".repeat(maxSlug + maxVer + 12)}`);
    for (const p of plugins) {
      const status =
        p.status === "active"   ? `${GREEN}active${RESET}` :
        p.status === "broken"   ? `${RED}broken${RESET}` :
        `${YELLOW}${p.status}${RESET}`;
      const enabled = p.enabled ? "" : ` ${DIM}[disabled]${RESET}`;
      console.log(
        `  ${p.slug.padEnd(maxSlug)}  ${p.version.padEnd(maxVer)}  ${status}${enabled}`
      );
    }
    console.log();
  } catch (e) {
    console.error(`  ${RED}List failed: ${e.message}${RESET}\n`);
    process.exit(1);
  }
}

async function cmdUninstall(args) {
  const slug = args[0];
  if (!slug) {
    console.error(`  ${RED}Usage: plugin uninstall <slug>${RESET}\n`);
    process.exit(1);
  }
  console.log(`  ${BOLD}Uninstalling:${RESET} ${slug}\n`);
  try {
    await apiRequest("DELETE", `/plugins/${slug}`);
    console.log(`  ${GREEN}✓${RESET} Uninstalled: ${slug}\n`);
  } catch (e) {
    console.error(`  ${RED}Uninstall failed: ${e.message}${RESET}\n`);
    process.exit(1);
  }
}

async function cmdUpdate(args) {
  const slug = args[0];
  if (!slug) {
    console.error(`  ${RED}Usage: plugin update <slug>${RESET}\n`);
    process.exit(1);
  }
  console.log(`  ${BOLD}Updating:${RESET} ${slug}\n`);
  try {
    // Update in place — preserves SQL layer, copies new knowledge layer files
    const result = await apiRequest("POST", `/plugins/${slug}/update`, {});
    console.log(`  ${GREEN}✓${RESET} Updated ${BOLD}${slug}${RESET}: ${result.from_version} → ${result.to_version}\n`);
  } catch (e) {
    console.error(`  ${RED}Update failed: ${e.message}${RESET}\n`);
    process.exit(1);
  }
}

function cmdInit(args) {
  const rawName = args[0] ?? "my-plugin";
  // Sanitise to slug
  const slug = rawName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
  const targetDir = resolve(process.cwd(), slug);

  if (existsSync(targetDir)) {
    console.error(`  ${RED}Directory '${slug}' already exists.${RESET}\n`);
    process.exit(1);
  }

  if (!existsSync(SKELETON_DIR)) {
    console.error(`  ${RED}Skeleton template not found at: ${SKELETON_DIR}${RESET}\n`);
    process.exit(1);
  }

  // Copy skeleton recursively, replacing __SLUG__ placeholder
  copyDir(SKELETON_DIR, targetDir, slug);

  console.log(`
  ${GREEN}✓${RESET} Plugin scaffold created: ${BOLD}${slug}/${RESET}

  ${BOLD}Next steps:${RESET}
  ${CYAN}1.${RESET} cd ${slug}/
  ${CYAN}2.${RESET} Edit ${BOLD}plugin.yaml${RESET} — set your id, name, description, author
  ${CYAN}3.${RESET} Implement your capabilities under agents/, skills/, etc.
  ${CYAN}4.${RESET} Install locally: ${BOLD}npx @evoapi/evo-nexus plugin install <path>${RESET}
`);
}

function copyDir(src, dest, slug) {
  mkdirSync(dest, { recursive: true });
  for (const entry of readdirSync(src, { withFileTypes: true })) {
    const srcPath = join(src, entry.name);
    const destName = entry.name.replace(/__SLUG__/g, slug);
    const destPath = join(dest, destName);
    if (entry.isDirectory()) {
      copyDir(srcPath, destPath, slug);
    } else {
      let content = readFileSync(srcPath, "utf-8");
      content = content.replace(/__SLUG__/g, slug);
      writeFileSync(destPath, content);
    }
  }
}

// ── Help ──────────────────────────────────────────────────────────────────────

function showHelp() {
  console.log(`
  ${BOLD}EvoNexus Plugin CLI${RESET}

  ${BOLD}Usage:${RESET}
    npx @evoapi/evo-nexus plugin <subcommand> [args]

  ${BOLD}Subcommands:${RESET}
    ${CYAN}install <url>${RESET}      Install a plugin from an HTTPS URL
    ${CYAN}list${RESET}               List installed plugins
    ${CYAN}uninstall <slug>${RESET}   Uninstall a plugin by slug
    ${CYAN}update <slug>${RESET}      Update a plugin (uninstall + reinstall)
    ${CYAN}init [name]${RESET}        Scaffold a new plugin in ./<name>/

  ${BOLD}Environment:${RESET}
    EVONEXUS_API_URL      Dashboard URL (default: http://localhost:8080)
    DASHBOARD_API_TOKEN   Bearer token for authenticated requests

  ${BOLD}Examples:${RESET}
    npx @evoapi/evo-nexus plugin install https://github.com/org/my-plugin
    npx @evoapi/evo-nexus plugin list
    npx @evoapi/evo-nexus plugin init my-crm-plugin
`);
}

// ── Entry point ───────────────────────────────────────────────────────────────

export async function runPlugin(args) {
  const sub = args[0];
  const rest = args.slice(1);

  switch (sub) {
    case "install":   return cmdInstall(rest);
    case "list":      return cmdList();
    case "uninstall": return cmdUninstall(rest);
    case "update":    return cmdUpdate(rest);
    case "init":      return cmdInit(rest);
    case "--help":
    case "-h":
    case undefined:
      showHelp();
      break;
    default:
      console.error(`  ${RED}Unknown plugin subcommand: ${sub}${RESET}`);
      showHelp();
      process.exit(1);
  }
}
