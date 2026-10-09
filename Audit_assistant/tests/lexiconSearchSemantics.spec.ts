import { expect, test } from "@playwright/test";
import { readFileSync } from "node:fs";
import type { DraftLexiconContent } from "../src/types/investigationCreation";
import {
  projectLexiconSearchTerms,
  validateLexiconDraft
} from "../src/features/investigation/lexiconDraft";

const cases: { name: string; content: DraftLexiconContent; expected_terms: string[] }[] =
  JSON.parse(readFileSync(new URL("../../tests/fixtures/lexicon_search_semantics.json", import.meta.url), "utf8"));

for (const scenario of cases) {
  test(`lexicon selection: ${scenario.name}`, () => {
    const content = scenario.content as DraftLexiconContent;
    expect(projectLexiconSearchTerms(content)).toEqual(scenario.expected_terms);
    const errors = validateLexiconDraft(content);
    if (scenario.expected_terms.length) {
      expect(errors).toEqual([]);
    } else {
      expect(errors).toEqual(["至少需要一个可用于搜索的启用词条"]);
    }
  });
}

test("fallback does not accept blank terms or orphan variants", () => {
  const body = structuredClone(cases[2].content) as DraftLexiconContent;
  body.entries[0].term = " ";
  body.entries[1].parent_id = "missing-topic";
  const errors = validateLexiconDraft(body);
  expect(errors).toContain("主题名称不能为空");
  expect(errors).toContain("词条不能为空");
  expect(errors.some(error => error.includes("尚未归入有效主题"))).toBe(true);
});
