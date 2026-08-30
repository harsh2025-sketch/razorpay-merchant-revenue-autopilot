import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

// jsdom has no clipboard implementation; provide a minimal one so CopyButton
// tests exercise the real handler.
if (typeof navigator !== "undefined" && !navigator.clipboard) {
  Object.defineProperty(navigator, "clipboard", {
    value: {
      writeText: async () => undefined,
    },
    configurable: true,
  });
}

// jsdom has no ResizeObserver; recharts' ResponsiveContainer needs one that
// reports a stable surface so the two product charts lay out synchronously.
class ResizeObserverStub {
  private callback: (entries: unknown[]) => void;

  constructor(callback: (entries: unknown[]) => void) {
    this.callback = callback;
  }

  observe(target: Element) {
    this.callback([
      {
        target,
        contentRect: { width: 800, height: 240, x: 0, y: 0, top: 0, left: 0 },
      },
    ]);
  }

  unobserve() {}

  disconnect() {}
}

if (typeof globalThis.ResizeObserver === "undefined") {
  (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
    ResizeObserverStub;
}

// Deterministic layout measurements inside jsdom.
if (typeof window !== "undefined") {
  window.matchMedia = window.matchMedia ?? (() => ({
    matches: false,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: () => false,
  }));
}

