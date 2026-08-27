# R0.2 API and SSE Compatibility

`backend.main` now instantiates `HermesInvestigationAgentService`. It retains
the existing `InvestigationStore`, public request/response DTOs, executor, and
SSE router while turn execution enters `HermesRuntimeBinding`, `AIAgent`, and
the final `hermes_m0` plugin.

Verified unchanged public routes include report session creation, session
turn submission, turn lookup, resume, message history, and SSE events. The
existing sequence/replay/resume tests passed. Focused R0.2 tests prove:

- completed client-message replay executes AIAgent once;
- interrupted/unknown outcomes remain retryable;
- completed turn replay returns the persisted result;
- each Session is permanently anchored to one ReportVersion/FrozenSnapshot;
- the Product Store unique running-Turn constraint rejects same-Session overlap;
- different Sessions execute AIAgent and Tool calls concurrently without a
  process-wide execution lock;
- public Message and SSE projections exclude private Hermes ToolResult and
  tool-call identifiers.

The legacy `backend/investigation` implementation remains in the tree for
contracts and storage compatibility, but the formal route no longer
instantiates `InvestigationAgentService`. No M3 collection or task-parameter
workflow was introduced.
