# Design

## Source of truth
Active, 2026-10-15. Scope: investigation draft geography editor. Evidence: user screenshot, `RelevanceScopeEditor.tsx`, `investigation-workspace.css`, geographic UI regression tests. No existing repository design brief was found.

## Brand
Calm, clear, practical. Reuse the workspace blue and slate palette. Avoid oversized panels, browser-default buttons and decorative effects.

## Product goals
Make the current region easy to identify and a region change easy to complete. Preserve catalog validation, immutable history and resource isolation. Starting collection is outside this change.

## Personas and jobs
An investigator reviewing a draft needs to confirm or correct its province/city without reading implementation details.

## Information architecture
Current region and edit action form the header. Selection policy is secondary. An inline edit panel contains candidates, a manual entry field and save/cancel actions. Readiness information follows the panel.

## Design principles
Show one clear primary action. Distinguish editing a resolved region from resolving ambiguity. Preserve the current value until a successful save. Avoid repeating the same error.

## Visual language
Reuse existing slate text and blue interactive colors. Use 12–14px supporting text, 16px region value, 8/12/16px spacing, 8–10px corners and light borders. Use existing lucide icons, no new assets or dependencies.

## Components
Keep `RelevanceScopeEditor` as the shared suggestion/confirmation editor. Scope styles to this component; keep API and callback contracts unchanged. Use consistent candidate, secondary and primary button variants.

## Accessibility
Retain accessible section and input names. Decorative icons are hidden from assistive technology. Buttons have visible keyboard focus, disabled styling and explicit types. Input guidance is associated with its field; errors retain alert semantics.

## Responsive behavior
Header and candidate groups wrap. Manual entry is capped at a readable width on wide screens. Below 560px, actions move below the full-width input; no horizontal overflow at 390px.

## Interaction states
Collapsed: current value and edit action. Editing: neutral change heading and manual input. Pending: actual clarification question and candidates. Saving: controls locked with visible status. Error: retained input and one visible message. Success: return to current value with updated draft.

## Content voice
Use direct labels. Ordinary changes say “修改调查地域”; use “指哪个调查范围” only for pending resolution. Saving updates the draft; it does not start collection.

## Implementation constraints
React/TypeScript, existing CSS and lucide-react. No backend, prompt or resource changes. Verify geographic interaction tests, typecheck/build, and real browser appearance in collapsed and editing states.

## Open questions
None blocking this bounded UI change. Assumption: the existing blue/slate workspace appearance remains the visual baseline.
