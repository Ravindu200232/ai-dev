"""The smallest Next.js project that builds, tests and runs end to end."""
from __future__ import annotations

import json
from pathlib import Path

DEPENDENCIES = {
    "next": "^15.5.4",
    "react": "^19.1.0",
    "react-dom": "^19.1.0",
}

DEV_DEPENDENCIES = {
    "@playwright/test": "^1.56.0",
    "@testing-library/jest-dom": "^6.6.3",
    "@testing-library/react": "^16.3.0",
    "@testing-library/user-event": "^14.5.2",
    "@types/node": "^22.10.0",
    "@types/react": "^19.1.0",
    "@types/react-dom": "^19.1.0",
    "@vitejs/plugin-react": "^4.3.4",
    "jsdom": "^25.0.1",
    "typescript": "^5.7.2",
    "vitest": "^3.2.4",
}

SCRIPTS = {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "next lint",
    "test": "vitest run",
    "test:unit": "vitest run",
    "test:e2e": "playwright test",
}


def _package(name: str) -> str:
    return json.dumps({
        "name": name, "version": "0.1.0", "private": True,
        "scripts": SCRIPTS,
        "dependencies": DEPENDENCIES, "devDependencies": DEV_DEPENDENCIES,
    }, indent=2) + "\n"


TSCONFIG = json.dumps({
    "compilerOptions": {
        "target": "ES2022", "lib": ["dom", "dom.iterable", "esnext"],
        "allowJs": True, "skipLibCheck": True, "strict": True,
        "noEmit": True, "esModuleInterop": True, "module": "esnext",
        "moduleResolution": "bundler", "resolveJsonModule": True,
        "isolatedModules": True, "jsx": "preserve", "incremental": True,
        "plugins": [{"name": "next"}], "paths": {"@/*": ["./*"]},
    },
    "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
    "exclude": ["node_modules"],
}, indent=2) + "\n"

VITEST_CONFIG = '''import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
  },
  resolve: { alias: { "@": path.resolve(__dirname, ".") } },
});
'''

VITEST_SETUP = '''import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => cleanup());
'''

PLAYWRIGHT_CONFIG = '''import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "tests/e2e",
  timeout: 30_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["json", { outputFile: "test-results/e2e.json" }], ["list"]],
  use: { baseURL, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
'''

LAYOUT = '''import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "%(title)s" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body data-testid="app-body">{children}</body>
    </html>
  );
}
'''

PAGE = '''export default function Home() {
  return (
    <main data-testid="home">
      <h1>%(title)s</h1>
      <p>This scaffold builds. The plan decides what replaces it.</p>
    </main>
  );
}
'''

GLOBALS = """*, *::before, *::after { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, sans-serif; line-height: 1.5; }
main { max-width: 62rem; margin: 0 auto; padding: 2rem 1.25rem; }
"""

GITIGNORE = "node_modules\n.next\nout\ntest-results\nplaywright-report\n.env*.local\n*.report.json\n"

NEXT_CONFIG = ('import type { NextConfig } from "next";\n\n'
               'const nextConfig: NextConfig = { reactStrictMode: true };\n\n'
               'export default nextConfig;\n')


def files(name: str = "app", title: str = "New App") -> dict:
    """Every scaffold file as `{relative path: contents}`."""
    return {
        "package.json": _package(name),
        "tsconfig.json": TSCONFIG,
        "next.config.ts": NEXT_CONFIG,
        "next-env.d.ts": '/// <reference types="next" />\n'
                         '/// <reference types="next/image-types/global" />\n',
        ".gitignore": GITIGNORE,
        "app/layout.tsx": LAYOUT % {"title": title},
        "app/page.tsx": PAGE % {"title": title},
        "app/globals.css": GLOBALS,
        "vitest.config.ts": VITEST_CONFIG,
        "vitest.setup.ts": VITEST_SETUP,
        "playwright.config.ts": PLAYWRIGHT_CONFIG,
    }


def scaffold(project_dir, name: str = "app", title: str = "New App",
             overwrite: bool = False) -> list:
    """Write the scaffold. Existing files are left alone unless told otherwise."""
    root = Path(project_dir)
    written = []
    for relative, content in files(name, title).items():
        target = root / relative
        if target.exists() and not overwrite:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)
    for folder in ("tests/unit", "tests/e2e", "components", "lib"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    return written
