import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * Visual/compliance guardrails enforced at source level:
 * - no starter-template or vendor branding
 * - no alert()/confirm()/raw prompt dialogs
 * - no marketing words, blockchain mentions, or fake metrics
 * - exactly two chart implementations (both recharts)
 * - no banned icon usage (Brain/Bot/Sparkles/…) and no emoji
 */

function walk(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else out.push(full);
  }
  return out;
}

const SOURCE_DIRS = ["app", "components", "lib"];
const sourceFiles = SOURCE_DIRS.flatMap((dir) =>
  walk(join(__dirname, "..", dir)),
).filter((file) => /\.(ts|tsx|css)$/.test(file));

const allSource = sourceFiles
  .map((file) => readFileSync(file, "utf8"))
  .join("\n");

describe("forbidden elements", () => {
  it("contains no vendor or starter-template branding", () => {
    expect(allSource).not.toMatch(/vercel/i);
    expect(allSource).not.toMatch(/next\.js/i);
    expect(allSource).not.toMatch(/shadcn/i);
    expect(allSource).not.toMatch(/powered by/i);
  });

  it("never uses alert/confirm/prompt dialogs", () => {
    expect(allSource).not.toMatch(/\balert\(/);
    expect(allSource).not.toMatch(/\bconfirm\(/);
    expect(allSource).not.toMatch(/\bprompt\(/);
  });

  it("never mentions blockchain or fake business metrics", () => {
    expect(allSource).not.toMatch(/blockchain/i);
    expect(allSource).not.toMatch(/revenue recovered/i);
    expect(allSource).not.toMatch(/\bprofit\b/i);
    expect(allSource).not.toMatch(/\bROI\b/);
    expect(allSource).not.toMatch(/AI accuracy/i);
  });

  it("uses no banned decorative icons", () => {
    const banned = [
      /\bBrain\b/,
      /\bBot\b/,
      /\bSparkles\b/,
      /\bRocket\b/,
      /\bWand2?\b/,
      /\bZap\b/,
    ];
    for (const pattern of banned) {
      expect(allSource).not.toMatch(pattern);
    }
  });

  it("implements exactly two recharts chart components", () => {
    const chartFiles = sourceFiles.filter((file) => {
      const content = readFileSync(file, "utf8");
      return content.includes("from \"recharts\"");
    });
    expect(chartFiles).toHaveLength(2);
    expect(chartFiles.some((f) => f.endsWith("segment-conversion-chart.tsx"))).toBe(true);
    expect(chartFiles.some((f) => f.endsWith("statistical-result.tsx"))).toBe(true);
  });

  it("uses no emoji anywhere in the UI source", () => {
    const emoji = /[\u{1F300}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/u;
    expect(allSource).not.toMatch(emoji);
  });

  it("keeps the top navigation to the three frozen destinations", () => {
    const nav = readFileSync(
      join(__dirname, "..", "components", "top-nav.tsx"),
      "utf8",
    );
    expect(nav).toContain('"/overview"');
    expect(nav).toContain('"/autopilot"');
    expect(nav).toContain('"/audit"');
    expect(nav).not.toMatch(/opportunit(exclusive)?ies"/i);
    expect(nav).not.toMatch(/experiments"/i);
    expect(nav).not.toMatch(/sidebar/i);
  });
});
