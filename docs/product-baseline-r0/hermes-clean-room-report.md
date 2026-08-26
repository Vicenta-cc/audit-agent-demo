# Hermes R0.1 Clean-Room Verification

Required source:

- repository: `https://github.com/NousResearch/hermes-agent`
- commit: `e624e9fde561e1add9388384012b295fde669ade`
- expected version: `0.20.4`
- candidate requirement: `requirements-hermes.txt`

## Attempt

The verification was run from a fresh empty Git repository at
`/Users/ext.wanghongtao6/Documents/Codex/projects/hermes-r0-1-clone/source`.
No local `hermes-agent-main` source, candidate venv, or `PYTHONPATH` was used.

Commands:

```text
git init /Users/ext.wanghongtao6/Documents/Codex/projects/hermes-r0-1-clone/source
git remote add origin https://github.com/NousResearch/hermes-agent.git
git fetch --no-tags origin e624e9fde561e1add9388384012b295fde669ade
```

Result:

```text
fatal: unable to access 'https://github.com/NousResearch/hermes-agent.git/': Error in the HTTP2 framing layer
```

A second fetch retry against the same zero-object repository forced Git's
HTTP/1.1 transport and waited 75 seconds before failing with:

```text
fatal: unable to access 'https://github.com/NousResearch/hermes-agent.git/': Failed to connect to github.com port 443 after 75003 ms: Couldn't connect to server
```

The repository still contains zero fetched objects after both attempts, so checkout, package install,
key-source SHA-256 comparison, Python dependency resolution, and installed
`hermes_cli`/`model_tools`/`run_agent.AIAgent` verification could not run. This
is a network acquisition blocker, not evidence that the commit is absent.

No API key, provider request, or global Python environment change was made.
The candidate-local adapter/plugin remains covered by its offline fail-closed
smoke; it must be rerun against the exact installed distribution before an
accepted baseline can be declared.

## Gate status

**BLOCKED**: repeat the same fetch in a network-enabled environment, create a
new isolated venv, install the candidate requirement, record `python --version`
and resolved package versions, compare only the files registered in
`HERMES_RUNTIME_MANIFEST.md`, and run the adapter/plugin smoke. Do not replace
the official checkout with local source or modify it to match historical
hashes.
