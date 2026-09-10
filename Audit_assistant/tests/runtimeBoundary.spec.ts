import { expect, test } from "@playwright/test";
import { resolveM3ApiProxyTarget } from "../src/runtimeBoundary";

test("M3 proxy target rejects the legacy 8000 backend", () => {
  expect(() => resolveM3ApiProxyTarget("http://127.0.0.1:8000")).toThrow(
    "legacy backend port 8000"
  );
  expect(() => resolveM3ApiProxyTarget(undefined)).toThrow(
    "must be set explicitly"
  );
  expect(resolveM3ApiProxyTarget("http://127.0.0.1:8010/")).toBe(
    "http://127.0.0.1:8010"
  );
});
