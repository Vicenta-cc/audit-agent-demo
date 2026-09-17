import { expect, test } from "@playwright/test";
import { eventBelongsToInvestigationTurn } from "../src/services/investigations";

test("public stream events are accepted only for the requested Turn", () => {
  expect(eventBelongsToInvestigationTurn(
    { turn_id: "investigation-turn:current" },
    "investigation-turn:current"
  )).toBe(true);
  expect(eventBelongsToInvestigationTurn(
    { turn_id: "investigation-turn:another-session" },
    "investigation-turn:current"
  )).toBe(false);
});
