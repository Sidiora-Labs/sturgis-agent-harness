import { afterEach, beforeEach, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { cleanup, configure } from "@testing-library/react";
configure({ asyncUtilTimeout: 3000 });

beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
});
afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

// jsdom has no layout engine; browser journeys exercise the actual layout APIs.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  })),
});
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
Object.defineProperty(globalThis, "ResizeObserver", {
  configurable: true,
  writable: true,
  value: ResizeObserverStub,
});
Element.prototype.scrollIntoView = vi.fn();
Element.prototype.scrollTo = vi.fn();
