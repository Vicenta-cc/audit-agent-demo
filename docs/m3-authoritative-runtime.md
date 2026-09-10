# M3 authoritative runtime boundary

Run the current frontend and backend against one explicit, stable application
data directory. The directory must be backed up and must not be a temporary
test directory. For example, choose deployment-specific values in the shell
that starts the services:

```sh
export XHS_AUDIT_DATA_DIR="/path/to/stable/application-data"
export XHS_AUDIT_OUTPUTS_DIR="${XHS_AUDIT_DATA_DIR}/outputs"
export XHS_AUDIT_BACKEND_PORT="<backend-port>"
export VITE_API_PROXY_TARGET="http://127.0.0.1:${XHS_AUDIT_BACKEND_PORT}"
```

Start the latest backend with both data variables set, then start the latest
`Audit_assistant` frontend in the same shell so its API proxy targets that
backend. Do not point the latest frontend at an older process merely because
that process contains saved accounts.

The selected data directory is the application boundary for managed crawler
accounts, investigation Drafts and Runs, Jobs, ingestion state, and published
reports. Account authentication state remains encrypted in the backend store;
it must never be copied into frontend configuration, logs, fixtures, or source
control. Tests must use a separate `/tmp` data directory and synthetic account
state.
