import { expect, test } from "@playwright/test";
import { formatDetailDateTime } from "../src/features/task-outputs/taskOutputUtils";

const originalTimezone = process.env.TZ;
test.beforeAll(() => { process.env.TZ = "Asia/Shanghai"; });
test.afterAll(() => {
  if (originalTimezone === undefined) delete process.env.TZ;
  else process.env.TZ = originalTimezone;
});

test("post and comment ISO timestamps use local time including date rollover", () => {
  expect(formatDetailDateTime("2026-10-16T12:09:00Z")).toBe("10-16 20:09");
  expect(formatDetailDateTime("2026-10-16T20:09:00+08:00")).toBe("10-16 20:09");
  expect(formatDetailDateTime("2026-10-16T07:09:00-0500")).toBe("10-16 20:09");
  expect(formatDetailDateTime("2026-12-31T20:09:00.123Z")).toBe("01-01 04:09");
});

test("legacy, missing and invalid timestamps retain their previous display", () => {
  expect(formatDetailDateTime("2026-10-16T12:09:00")).toBe("10-16 12:09");
  expect(formatDetailDateTime("2026-10-16 12:09:00")).toBe("10-16 12:09");
  expect(formatDetailDateTime("")).toBe("刚刚");
  expect(formatDetailDateTime("invalid-date")).toBe("id-date");
  expect(formatDetailDateTime("2026-13-16T12:09:00Z")).toBe("13-16 12:09");
});

test("local timezone is respected rather than hardcoding the Shanghai offset", () => {
  process.env.TZ = "UTC";
  try {
    expect(formatDetailDateTime("2026-10-16T12:09:00Z")).toBe("10-16 12:09");
    expect(formatDetailDateTime("2026-10-16T20:09:00+08:00")).toBe("10-16 12:09");
  } finally {
    process.env.TZ = "Asia/Shanghai";
  }
});
