# Tests

Three groups, and the one thing they share is `api_routes.py`.

| Group | What it covers | Needs Docker |
|---|---|---|
| `unit/` | the pieces in isolation — persistence, dispatcher, retry, CLI, client | no |
| `local_e2e/` | the HTTP contract of all 26 routes, against the real app in-process | no |
| `fullstack/` | delivery behaviour and load, on the compose stack — the stress suite | yes |

`api_routes.py` holds every route path the suites call. A path written by hand
in a test is a duplicate of the API's own contract, so it lives here once: a
transport change edits this module, and whatever still fails afterwards is a
real regression.

Run `local_e2e` for transport and contract changes — it boots the real
application on a temporary SQLite file and serves it with uvicorn on a free
port, so it runs on any machine in about two seconds. Run `fullstack` for
delivery behaviour; see `fullstack/README.md`, including its measured notes on
the stack's memory and port behaviour.

The CI selection is `pytest -m "not fullstack and not db"`, which is `unit` and
`local_e2e` together in one process.

## Test-Infrastructure Gotchas

Three things that are silent when they go wrong, so they are written down.

### `pytest.ini` wins over `pyproject.toml`, completely

When a `pytest.ini` exists, pytest reads it and ignores
`[tool.pytest.ini_options]` in `pyproject.toml` entirely — not merged, ignored.
This project keeps its whole pytest configuration in `pytest.ini` for that
reason, and `pyproject.toml` carries a comment saying so. A `testpaths`,
`addopts` or `markers` entry added to `pyproject.toml` would never be applied,
and the failure is silent: coverage flags declared there simply never run.

### `pytestmark` in a `conftest.py` marks nothing

pytest honours `pytestmark` in test modules and classes only. A package-wide
marker declared in a `conftest.py` looks right, reads right, and does nothing —
here it left seven Docker-dependent tests escaping `-m "not fullstack"` and
erroring in CI. Declare markers per module, or apply them from a
`pytest_collection_modifyitems` hook.

### `create_app()` mutates module globals unless given a `lifespan`

`mail_proxy.api.create_app()` sets the module-level `service` global, and
without a `lifespan` argument it registers its routes on the module-level
`app` object rather than building a new one. Pass a `lifespan` to get a fresh
application — that is what makes an in-process harness possible, and it is why
`local_e2e/conftest.py` passes one. Omitting it makes separate suites share
one global application and one global service.
