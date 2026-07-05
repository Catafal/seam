import { spawn, spawnSync, type ChildProcessWithoutNullStreams } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { mkdir } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createServer } from "node:net";

export interface ExplorerServer {
  url: string;
  fixtureRoot: string;
  stop: () => Promise<void>;
}

const REPO_ROOT = resolve(fileURLToPath(new URL("../../../..", import.meta.url)));

async function freePort(): Promise<number> {
  return new Promise((resolvePort, reject) => {
    const server = createServer();
    server.unref();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (!address || typeof address === "string") {
          reject(new Error("Could not allocate a local TCP port"));
          return;
        }
        resolvePort(address.port);
      });
    });
  });
}

function writeFixture(root: string): void {
  const appDir = join(root, "app");
  writeFileSync(join(root, "pyproject.toml"), "[project]\nname = \"seam-visual-fixture\"\nversion = \"0.0.0\"\n", "utf8");
  writeFileSync(join(appDir, "__init__.py"), "", "utf8");
  writeFileSync(
    join(appDir, "models.py"),
    [
      "class User:",
      "    def __init__(self, name: str) -> None:",
      "        self.name = name",
      "",
      "class AuditEvent:",
      "    def __init__(self, actor: User, action: str) -> None:",
      "        self.actor = actor",
      "        self.action = action",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(appDir, "services.py"),
    [
      "from .models import AuditEvent, User",
      "",
      "def normalize_name(name: str) -> str:",
      "    return name.strip().title()",
      "",
      "def load_user(raw_name: str) -> User:",
      "    return User(normalize_name(raw_name))",
      "",
      "def record_login(raw_name: str) -> AuditEvent:",
      "    user = load_user(raw_name)",
      "    return AuditEvent(user, \"login\")",
      "",
      "def describe_event(event: AuditEvent) -> str:",
      "    return f\"{event.actor.name}:{event.action}\"",
      "",
    ].join("\n"),
    "utf8",
  );
  writeFileSync(
    join(appDir, "api.py"),
    [
      "from .services import describe_event, record_login",
      "",
      "def login_view(name: str) -> dict[str, str]:",
      "    event = record_login(name)",
      "    return {\"summary\": describe_event(event)}",
      "",
    ].join("\n"),
    "utf8",
  );
}

function runIndex(root: string): void {
  const result = spawnSync("uv", ["run", "seam", "init", root], {
    cwd: REPO_ROOT,
    encoding: "utf8",
    env: { ...process.env, NO_PROXY: "127.0.0.1,localhost" },
  });

  if (result.status !== 0) {
    throw new Error(
      [
        `Failed to index visual QA fixture at ${root}`,
        result.stdout,
        result.stderr,
      ].join("\n"),
    );
  }
}

async function waitForStatus(url: string, process: ChildProcessWithoutNullStreams): Promise<void> {
  const deadline = Date.now() + 45_000;
  let lastError = "";

  while (Date.now() < deadline) {
    if (process.exitCode !== null) {
      throw new Error(`seam serve exited early with code ${process.exitCode}: ${lastError}`);
    }

    try {
      const response = await fetch(`${url}/api/status`);
      if (response.ok) return;
      lastError = `HTTP ${response.status}`;
    } catch (error) {
      lastError = error instanceof Error ? error.message : String(error);
    }

    await new Promise((resolveDelay) => setTimeout(resolveDelay, 300));
  }

  throw new Error(`Timed out waiting for ${url}/api/status: ${lastError}`);
}

export async function startExplorerServer(): Promise<ExplorerServer> {
  const fixtureRoot = mkdtempSync(join(tmpdir(), "seam-topology-visual-"));
  await mkdir(join(fixtureRoot, "app"), { recursive: true });
  writeFixture(fixtureRoot);
  runIndex(fixtureRoot);

  const port = await freePort();
  const url = `http://127.0.0.1:${port}`;
  const server = spawn(
    "uv",
    [
      "run",
      "--extra",
      "web",
      "seam",
      "serve",
      fixtureRoot,
      "--no-open",
      "--no-init",
      "--host",
      "127.0.0.1",
      "--port",
      String(port),
    ],
    {
      cwd: REPO_ROOT,
      env: {
        ...process.env,
        HTTP_PROXY: "",
        HTTPS_PROXY: "",
        ALL_PROXY: "",
        NO_PROXY: "127.0.0.1,localhost",
      },
    },
  );

  let output = "";
  server.stdout.on("data", (chunk) => {
    output += chunk.toString();
  });
  server.stderr.on("data", (chunk) => {
    output += chunk.toString();
  });

  try {
    await waitForStatus(url, server);
  } catch (error) {
    server.kill();
    rmSync(fixtureRoot, { recursive: true, force: true });
    throw new Error(`${error instanceof Error ? error.message : String(error)}\n${output}`);
  }

  return {
    url,
    fixtureRoot,
    stop: async () => {
      if (server.exitCode === null) {
        server.kill("SIGTERM");
        await new Promise<void>((resolveStop) => {
          const timeout = setTimeout(() => {
            server.kill("SIGKILL");
            resolveStop();
          }, 5_000);
          server.once("exit", () => {
            clearTimeout(timeout);
            resolveStop();
          });
        });
      }
      rmSync(fixtureRoot, { recursive: true, force: true });
    },
  };
}
