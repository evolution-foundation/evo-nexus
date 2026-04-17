#!/usr/bin/env node

/**
 * @evoapi/evo-nexus CLI
 *
 * Usage:
 *   npx @evoapi/evo-nexus          — clone + setup in current dir
 *   npx @evoapi/evo-nexus my-workspace  — clone into my-workspace/
 *   npx @evoapi/evo-nexus --help
 */

import { execSync, spawn } from "child_process";
import { existsSync } from "fs";
import { resolve, basename } from "path";
import { createInterface } from "readline";

const REPO = "https://github.com/EvolutionAPI/evo-nexus.git";
const GREEN = "\x1b[92m";
const CYAN = "\x1b[96m";
const YELLOW = "\x1b[93m";
const RED = "\x1b[91m";
const BOLD = "\x1b[1m";
const DIM = "\x1b[2m";
const RESET = "\x1b[0m";

function banner() {
  console.log(`
${GREEN}  ╔══════════════════════════════════════╗
  ║   ${BOLD}EvoNexus — Installer${RESET}${GREEN}            ║
  ║   ${DIM}Unofficial toolkit for Claude Code${RESET}${GREEN}  ║
  ╚══════════════════════════════════════╝${RESET}
`);
}

function run(cmd, opts = {}) {
  return execSync(cmd, { stdio: "inherit", ...opts });
}

function check(cmd) {
  try {
    execSync(cmd, { stdio: "pipe" });
    return true;
  } catch {
    return false;
  }
}

function ask(question) {
  const rl = createInterface({ input: process.stdin, output: process.stdout });
  return new Promise((resolve) => {
    rl.question(`  ${CYAN}>${RESET} ${question}`, (answer) => {
      rl.close();
      resolve(answer.trim());
    });
  });
}

async function main() {
  const args = process.argv.slice(2);

  if (args.includes("--help") || args.includes("-h")) {
    console.log(`
  Usage: npx @evoapi/evo-nexus [directory]

  Clones the EvoNexus repository and runs the interactive setup wizard.

  Arguments:
    directory    Target directory (default: evonexus)

  Options:
    --help, -h   Show this help
    --version    Show version

  Examples:
    npx @evoapi/evo-nexus
    npx @evoapi/evo-nexus my-workspace
`);
    process.exit(0);
  }

  if (args.includes("--version")) {
    const { readFileSync } = await import("fs");
    const { fileURLToPath } = await import("url");
    const { dirname, join } = await import("path");
    const dir = dirname(fileURLToPath(import.meta.url));
    const pkg = JSON.parse(readFileSync(join(dir, "..", "package.json"), "utf-8"));
    console.log(pkg.version);
    process.exit(0);
  }

  banner();

  // ── Check prerequisites ────────────────────
  console.log(`  ${BOLD}Checking prerequisites...${RESET}\n`);

  const checks = [
    { name: "git", cmd: "git --version", install: "https://git-scm.com" },
    { name: "Claude Code", cmd: "claude --version", install: "npm install -g @anthropic-ai/claude-code" },
    { name: "Node.js", cmd: "node --version", install: "https://nodejs.org" },
    { name: "Python 3", cmd: "python3 --version", install: "https://python.org" },
  ];

  let missing = [];
  for (const { name, cmd, install } of checks) {
    if (check(cmd)) {
      const version = execSync(cmd, { encoding: "utf-8" }).trim();
      console.log(`  ${GREEN}✓${RESET} ${name}: ${DIM}${version}${RESET}`);
    } else {
      console.log(`  ${RED}✗${RESET} ${name}: ${RED}not found${RESET} — ${DIM}${install}${RESET}`);
      missing.push(name);
    }
  }

  // uv (optional but recommended)
  if (check("uv --version")) {
    const version = execSync("uv --version", { encoding: "utf-8" }).trim();
    console.log(`  ${GREEN}✓${RESET} uv: ${DIM}${version}${RESET}`);
  } else {
    console.log(`  ${YELLOW}!${RESET} uv: ${DIM}not found (optional, will use pip)${RESET}`);
  }

  console.log();

  if (missing.length > 0) {
    console.log(`  ${RED}Missing required tools: ${missing.join(", ")}${RESET}`);
    console.log(`  ${YELLOW}Install them and try again.${RESET}\n`);
    process.exit(1);
  }

  // ── Clone ──────────────────────────────────
  // Filter out subcommands — "install" means install here (current dir or default name)
  const filteredArgs = args.filter(a => !["install", "init", "setup"].includes(a));
  const targetDir = filteredArgs[0] || ".";
  const targetPath = resolve(process.cwd(), targetDir);

  // Detect if target is an existing EvoNexus installation (git repo with pyproject.toml)
  const isExistingInstall = existsSync(resolve(targetPath, ".git")) && existsSync(resolve(targetPath, "pyproject.toml"));

  if (isExistingInstall) {
    // ── Update mode ─────────────────────────────
    console.log(`  ${GREEN}Existing EvoNexus installation detected.${RESET}\n`);

    // Show current version
    try {
      const { readFileSync } = await import("fs");
      const pyproject = readFileSync(resolve(targetPath, "pyproject.toml"), "utf-8");
      const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
      if (match) console.log(`  Current version: ${DIM}${match[1]}${RESET}`);
    } catch {}

    // Stop running services before updating
    console.log(`  ${DIM}Stopping services...${RESET}`);
    try { run("pkill -f 'terminal-server/bin/server.js' 2>/dev/null || true", { cwd: targetPath }); } catch {}
    try { run("pkill -f 'app.py' 2>/dev/null || true", { cwd: targetPath }); } catch {}

    // Pull latest (shallow-safe)
    console.log(`\n  ${BOLD}Pulling latest changes...${RESET}\n`);
    // Detect current branch
    const branch = execSync("git rev-parse --abbrev-ref HEAD", { cwd: targetPath, encoding: "utf-8" }).trim();
    run(`git fetch --depth=1 origin ${branch}`, { cwd: targetPath });
    run(`git merge --ff-only origin/${branch}`, { cwd: targetPath });

    // Show new version
    try {
      const { readFileSync } = await import("fs");
      const pyproject = readFileSync(resolve(targetPath, "pyproject.toml"), "utf-8");
      const match = pyproject.match(/^version\s*=\s*"([^"]+)"/m);
      if (match) console.log(`\n  Updated to: ${GREEN}${BOLD}${match[1]}${RESET}`);
    } catch {}

    console.log();
  } else if (targetDir === ".") {
    // Clone into current directory
    const { readdirSync } = await import("fs");
    const files = readdirSync(targetPath).filter(f => !f.startsWith("."));
    if (files.length > 0) {
      const answer = await ask(`${YELLOW}Current directory is not empty (${files.length} items). Clone here anyway? [y/N]: ${RESET}`);
      if (!answer.toLowerCase().startsWith("y")) {
        console.log(`  ${DIM}Aborted.${RESET}\n`);
        process.exit(0);
      }
    }
    console.log(`  ${BOLD}Cloning EvoNexus into current directory...${RESET}\n`);
    run(`git clone --depth=1 ${REPO} .`, { cwd: targetPath });
    console.log();
  } else if (existsSync(targetPath)) {
    const answer = await ask(`${YELLOW}Directory '${targetDir}' already exists. Continue anyway? [y/N]: ${RESET}`);
    if (!answer.toLowerCase().startsWith("y")) {
      console.log(`  ${DIM}Aborted.${RESET}\n`);
      process.exit(0);
    }
  } else {
    console.log(`  ${BOLD}Cloning EvoNexus...${RESET}\n`);
    run(`git clone --depth=1 ${REPO} "${targetPath}"`);
    console.log();
  }

  // ── Install dependencies ───────────────────
  console.log(`  ${BOLD}Installing dependencies...${RESET}\n`);

  // Python deps
  // Pass EVO_NEXUS_INSTALL=1 to signal setup.py that it is being run as
  // a pip build backend — this makes it skip the interactive wizard and
  // use setuptools.setup() for proper metadata (prevents EOFError when
  // pip runs without a TTY).
  const pipEnv = { ...process.env, EVO_NEXUS_INSTALL: "1" };
  if (check("uv --version")) {
    run("uv sync -q", { cwd: targetPath });
  } else {
    run("pip3 install -q -r requirements.txt 2>/dev/null || pip3 install -q -e .", {
      cwd: targetPath,
      env: pipEnv,
    });
  }

  // Frontend deps
  const frontendDir = resolve(targetPath, "dashboard", "frontend");
  if (existsSync(resolve(frontendDir, "package.json"))) {
    run("npm install --silent", { cwd: frontendDir });
    console.log(`\n  ${GREEN}✓${RESET} Frontend dependencies installed`);
  }

  // ── Update mode: rebuild + restart, skip setup wizard ─────
  if (isExistingInstall) {
    console.log(`\n  ${DIM}Building dashboard frontend...${RESET}`);
    try {
      run("npm run build --silent", { cwd: frontendDir });
      console.log(`  ${GREEN}✓${RESET} Dashboard rebuilt`);
    } catch {
      console.log(`  ${YELLOW}!${RESET} Frontend build failed — run: cd dashboard/frontend && npm run build`);
    }

    // Restart services — prefer systemd if available, fallback to start-services.sh
    const hasSystemd = check("systemctl is-active --quiet evo-nexus 2>/dev/null") ||
                       check("systemctl is-enabled --quiet evo-nexus 2>/dev/null");
    const startScript = resolve(targetPath, "start-services.sh");

    if (hasSystemd) {
      console.log(`\n  ${DIM}Restarting via systemd...${RESET}`);
      // If install dir differs from service dir, sync files
      try {
        const serviceDir = execSync("systemctl show evo-nexus -p WorkingDirectory --value 2>/dev/null", { encoding: "utf-8" }).trim();
        if (serviceDir && resolve(serviceDir) !== resolve(targetPath)) {
          console.log(`  ${DIM}Syncing to service directory ${serviceDir}...${RESET}`);
          run(`rsync -a --delete --exclude='.venv' --exclude='node_modules' --exclude='logs' --exclude='dashboard/data' "${targetPath}/" "${serviceDir}/"`, { cwd: targetPath });
          // Rebuild in service dir
          const svcFrontend = resolve(serviceDir, "dashboard", "frontend");
          if (existsSync(resolve(svcFrontend, "package.json"))) {
            run("npm install --silent && npm run build --silent", { cwd: svcFrontend });
          }
          // Fix ownership
          const serviceUser = execSync("systemctl show evo-nexus -p User --value 2>/dev/null", { encoding: "utf-8" }).trim();
          if (serviceUser) {
            run(`chown -R ${serviceUser}:${serviceUser} "${serviceDir}"`);
          }
        }
      } catch {}
      run("systemctl restart evo-nexus");
    } else if (existsSync(startScript)) {
      console.log(`\n  ${DIM}Restarting services...${RESET}`);
      run(`bash ${startScript}`, { cwd: targetPath });
    }

    // Wait and verify
    if (hasSystemd || existsSync(startScript)) {
      await new Promise(r => setTimeout(r, 3000));
      try {
        execSync("curl -sf http://localhost:8080/api/version", { timeout: 5000 });
        console.log(`  ${GREEN}✓${RESET} Dashboard restarted`);
      } catch {
        console.log(`  ${YELLOW}!${RESET} Dashboard may not have started — check: journalctl -u evo-nexus -n 20`);
      }
    }

    console.log(`\n  ${GREEN}${BOLD}EvoNexus updated successfully!${RESET}\n`);
    process.exit(0);
  }

  // ── Run setup wizard (fresh install only) ─────
  console.log(`\n  ${BOLD}Starting setup wizard...${RESET}\n`);

  const pythonCmd = check("uv --version") ? "uv run python" : "python3";
  const setup = spawn(pythonCmd.split(" ")[0], [...pythonCmd.split(" ").slice(1), "setup.py"], {
    cwd: targetPath,
    stdio: "inherit",
  });

  setup.on("close", (code) => {
    if (code === 0) {
      // Check if setup ran in remote mode (services already started)
      let isRemote = false;
      try {
        isRemote = existsSync("/etc/nginx/sites-enabled/evonexus");
      } catch {}

      if (!isRemote) {
        // Local mode: build frontend (setup already built, but ensure latest)
        console.log(`\n  ${DIM}Building dashboard frontend...${RESET}`);
        try {
          run("npm run build --silent", { cwd: frontendDir });
          console.log(`  ${GREEN}✓${RESET} Dashboard built\n`);
        } catch {
          console.log(`  ${YELLOW}!${RESET} Frontend build failed — run manually: cd ${targetDir}/dashboard/frontend && npm run build\n`);
        }
      }

      console.log(`
  ${GREEN}${BOLD}EvoNexus installed successfully!${RESET}
`);

      if (isRemote) {
        // Remote mode: services already running via systemd
        const hasSvc = check("systemctl is-enabled --quiet evo-nexus 2>/dev/null");
        if (hasSvc) {
          console.log(`  ${BOLD}The dashboard is running via systemd.${RESET}
  Open the URL shown above to create your admin account.

  ${BOLD}Useful commands:${RESET}
  ${CYAN}•${RESET} ${BOLD}systemctl restart evo-nexus${RESET}  — restart services
  ${CYAN}•${RESET} ${BOLD}systemctl status evo-nexus${RESET}   — check status
  ${CYAN}•${RESET} ${BOLD}journalctl -u evo-nexus -f${RESET}   — follow logs
  ${CYAN}•${RESET} ${BOLD}su - evonexus${RESET}                — switch to service user
`);
        } else {
          console.log(`  ${BOLD}The dashboard is already running.${RESET}
  Open the URL shown above to create your admin account.

  ${BOLD}Useful commands:${RESET}
  ${CYAN}•${RESET} ${BOLD}./start-services.sh${RESET}  — restart dashboard services
  ${CYAN}•${RESET} ${BOLD}make help${RESET}            — see all available commands
`);
        }
      } else {
        console.log(`  ${BOLD}Next steps:${RESET}
  ${CYAN}1.${RESET} cd ${targetDir}
  ${CYAN}2.${RESET} Edit ${BOLD}.env${RESET} with your API keys
  ${CYAN}3.${RESET} ${BOLD}make dashboard-app${RESET}    — start the dashboard
  ${CYAN}4.${RESET} Open ${BOLD}http://localhost:8080${RESET} and create your admin account
  ${CYAN}5.${RESET} ${BOLD}make help${RESET}             — see all available commands
`);
      }

      console.log(`  ${DIM}Documentation: https://evonexus.evolutionfoundation.com.br/docs${RESET}`);
      console.log(`  ${DIM}GitHub: https://github.com/EvolutionAPI/evo-nexus${RESET}\n`);
    } else {
      console.log(`\n  ${RED}Setup failed (exit code ${code}).${RESET}`);
      console.log(`  ${DIM}Try running manually: cd ${targetDir} && make setup${RESET}\n`);
      process.exit(code);
    }
  });
}

main().catch((err) => {
  console.error(`\n  ${RED}Error: ${err.message}${RESET}\n`);
  process.exit(1);
});
