# Next steps — configuration on genro-asgi

**Version**: 1.0
**Last Updated**: 2026-09-02
**Status**: 🔴 DA REVISIONARE

---

## Where this line stands

0.7.7 replaced FastAPI with genro-asgi as the HTTP transport. The v1 contract
did not change: the same 26 routes at the same addresses, the same bodies, the
same auth answers. It is merged on `develop` (PR #104).

The transport is now genro-asgi. **The configuration is not.** That is what this
document is about.

## The problem, measured

`MailProxy.__init__` (`src/mail_proxy/core/proxy.py:123`) accepts **27
parameters**. `src/mail_proxy/server.py:132` passes **two**:

```python
_core = MailProxy(
    db_path=_db_path,
    start_active=True,
)
```

The other **25** keep the defaults written in the signature, and in production
nothing can change them: not an environment variable, not the database, not a
file. Among them: `max_concurrent_sends`, `max_concurrent_per_account`,
`report_retention_seconds`, `max_retries`, `retry_delays`,
`batch_size_per_account`, `attachment_timeout`, the four `client_sync_*`, and
every queue size.

On top of that, three configuration mechanisms run in parallel:

| source | what it carries | where |
|---|---|---|
| env `GMP_*` | `GMP_DB_PATH`, `GMP_API_TOKEN`, six `GMP_BOUNCE_*` | `server.py:95-127` |
| database | `api_token`, the six `bounce_*` on the instance row | `server.py:96`, `172-187` |
| INI file | six attachment-cache settings | `config_loader.py` |

The third one is not even reachable: `config_loader.py` reads a `[cache]`
section from a `config.ini` and hands it to the core as `config_path`, but
`server.py` never passes `config_path`. It is a parallel loader with its own
`CacheConfig` dataclass, inert in production.

And two of them disagree on precedence, with nothing declaring it:

- `api_token` (`server.py:98`) — the environment wins over the database
- bounce IMAP (`server.py:138`, `_initialize_instance_from_env`) — the database
  wins, the environment only seeds the row the first time

Two module-level globals hold all this (`server.py:96-98`), populated at import
time through a synchronous database connection. Both contradict the project
rules: no globals at module level, state lives in instances.

## The model

Decided, and it is the frame every step below fits into.

1. The recipe (`config.py`) is read, **produces a Bag and hands it over**. Its
   job ends there. The `ConfigHandler` of genro-builders is read-only, and that
   is correct: writing does not go through it.
2. The Bag handed over is **ours**: it can be written as we like, and it can
   hold resolvers.
3. At boot the Bag is created by the recipe, then **actualised from the
   database**.
4. At runtime, configuration pages write **to the database and to the Bag**.
5. `Bag.subscribe(subscriber_id, update=, insert=, delete=, any=, transaction=)`
   notifies whoever subscribed when a value changes. `transaction` receives
   `bag=` and `mutations=<list>`, so a page writing several values notifies once
   with the list.

**The Bag is the single authority.** No component re-reads the database for
configuration.

The database is **not** for everything: it holds the *data* — tenants,
accounts, messages, logs. Everything else is *declared* in the recipe. Then,
for a chosen set of entries, the database carries variations.

The reason the declaration matters: it survives a database that is empty,
absent, or not yet migrated. A single point of reading is what keeps the values
from diverging between whoever reads the file and whoever reads the database.

## Steps

### 1. Read the genro-asgi documentation first

991 lines, and they are the source:

- `genro-asgi/docs/guides/configuration.md` (625) — the recipe, resolvers in
  place, layered defaults, what an application reads, an application declaring
  its own grammar, the sections, how to verify it, gotchas
- `genro-asgi/docs/concepts.md` (224) — the server/application model, code and
  mount, the demux rule, `auth_rule` and default-deny, design principles
- `genro-asgi/docs/architecture/overview.md` (142) — core principles, the two
  layers, "Configuration: the server reads its own"

Nothing below should be designed before these are read. Most of the questions
this document raises already have a written answer there.

### 2. Repair the parent chain into the core

`MailProxyApplication` reaches the server: `BaseApplication` carries a `server`
property with a setter, assigned once at attach time (the setter refuses
reassignment). So it can already read the Bag through
`self.server.config(f"applications.{self.code}.{path}")`.

`MailProxy` reaches nobody. No `self.server`, no `self.application`, no
`self.parent` — and neither do its children (`smtp_pool`, `rate_limit`). The
core is built at module level in `server.py:132` and passed *into* the
application as the `core` kwarg: the direction is inverted. Which is exactly
where the 25 unreachable parameters live.

`self.server` exists only **after** attach, never inside `__init__`, so the
configuration cannot be read in a constructor. `SpaApplication` solves it the
same way and says so: *"the whole orchestration is read at startup"*. The mail
proxy would read its own in `on_startup`, where it already starts the engine.

Two directions, **undecided — this is the first call to make**:

- **(a) the application builds the core** in `on_startup`, when it already has
  the Bag. The core is born configured, and the two module-level globals
  disappear with it. More invasive: `cli.py` builds the core on its own.
- **(b) the core receives the application** and reads its own section when told
  to start. Closer to today's construction order, but the core is built before
  it can know its own configuration.

### 3. Declare `MailProxyApplication`'s own grammar

`MailProxyApplication` (`src/mail_proxy/mail_proxy_application.py:77`) declares
`code` and `mount` but not `grammar`, so it inherits the base
`ApplicationGrammar`, whose only element is `parameters`. Its two real
parameters, `core` and `api_token`, enter through `kwargs.pop` (line 84) and are
invisible to any recipe.

The mechanism: the site's `applications.<code>` section **mounts the grammar the
`app_class` carries**. The precedent in genro-asgi is
`SpaApplicationGrammar` (`applications/spa_app.py:144`), which hangs a whole
`orchestration` subtree under its application because *"a pool belongs to the
application that owns it"*.

Authoring conventions, from `genro_asgi/config/elements.py`:

- attributes are **annotated**, so their signature defaults reach the read stack
- an attribute whose value may come from outside is annotated
  `<type> | BagResolver` and receives the resolver **in place** — this dialect
  has no `^pointer` strings
- the recipe orchestrates in `main` and delegates each section to a method
  taking the parent node

The name of the grammar class and of its nodes will be written in every
`config.py` of every site. They are named by the project owner, per rule 9 of
the parent CLAUDE.md: semantics plus two or three candidates, never a fait
accompli.

### 4. Absorb the three mechanisms into one

Everything that is *declaration* moves into the grammar: the 25 unreachable core
parameters, the six attachment-cache settings that today live in the INI, the
six bounce IMAP parameters, plus host, port, `middleware={"auth": False}` and
the database connection string.

`config_loader.py` and its `config.ini` then have no reason to exist: the
`[cache]` section becomes a node of the grammar.

Two values are **not** words of any grammar, because they must exist *before*
the database can be read: `db_path` and `api_token`. They travel as constructor
kwargs, the way `SpaApplication` treats `env_settings` — *"a dict the Python
recipe composes at runtime out of the environment it has already read"*.

`api_token` carries a security argument for staying out of the database
entirely: if the database can set it, whoever has write access to the database
grants themselves the service admin token. Worth deciding explicitly rather
than by default.

### 5. The configuration pages

genro-asgi already contains this pattern, built once, and it is the form to
reuse:

- `genro_asgi/orchestration_profile_store.py` — a **neutral** store: it imports
  nothing from `applications/` or `spa/`, so several callers read through the
  same component. It owns the delicate part: name validation, symlink refusal, a
  1 MiB limit in both directions, object-only JSON with non-finite literals
  rejected on read, and the atomic write.
- `genro_asgi/applications/configuration_profiles.py` — mounted at `_sysop`, it
  serves those profiles as a browser page, as REST under
  `/_sysop/configuration`, and as MCP tools at `/_sysop/mcp`.

The difference is only where it persists: that one writes a directory of JSON
files, the mail proxy wants the database. The neutral-store / mounting-application
split is what carries over.

### 6. The document that outlives this work

The goal is not only to fix the mail proxy's configuration. It is a **howto for
using genro-asgi** — configuration *and* how to organise classes — so that
applications and servers are always built by the same rules. It belongs in
genro-asgi, where it serves every project.

**The mail proxy is the first example of that howto**, which changes how it must
be written: every choice made here becomes a rule others copy, not just code
that works. Today `configuration.md` illustrates a per-application grammar with
`ShopGrammar`, an invented example. The mail proxy gives the tutorial a real
application — in production on four sites, 26 routes, an engine behind it — and
examples that can be executed and kept honest by CI.

What the existing documents do not cover, verified by search:

- **the runtime life of the Bag** — zero occurrences of `subscribe` and
  `trigger` in the whole configuration guide, which only ever mentions
  `BagResolver`
- **how a non-application class reaches the server** — precisely where the mail
  proxy breaks
- **the class-organisation rules gathered in one place**, instead of spread
  across `concepts.md` ("Design principles"), `architecture/overview.md` ("Core
  principles") and the genro-asgi `CLAUDE.md`

## Settled — do not reopen

- **A write path in `ConfigHandler`.** Considered and dropped: the handler is
  read-only by design (*"the source stays pure"*), and that is consistent with
  the model, because writing goes to the Bag and never through the handler. No
  change is needed in genro-builders.
- **Injecting the database through `parents`.** Considered and dropped: the
  docstring states *"Parents are recipes, never materialized documents"* and
  *"The datastore (`builder.data`) is not merged"*. The database holds values,
  not a recipe.
- **"Database first for everything, config as fallback".** The database is for
  data; configuration is declared, and only chosen entries take a variation from
  the database.
- **The API version as a path segment** — ADR-011, already in `ARCH.md`.

## Known issues to keep in view

- **[#79](https://github.com/genropy/genro-mail-proxy/issues/79)** — unified
  tenant resolution. `POST /commands/suspend` and `/commands/activate`
  (`src/mail_proxy/routers/command.py:60` and `:72`) take `tenant_id` from the
  query under a `TENANT` gate and never check it against the token's own tenant,
  so one tenant can hold another's sending. This is v1 verbatim, not something
  the transport swap introduced. Two things to settle there: the acceptance
  criteria say the helper goes in `api.py`, which no longer exists — the place is
  `mail_proxy_application.py`, next to `require_tenant_scope` and
  `get_token_tenant_id`; and `require_tenant_scope` raises 401 while the issue
  specifies 403 on mismatch.
- **`aiohttp.BasicAuth` is deprecated** and disappears in aiohttp 4.0. Used in
  `src/mail_proxy/core/reporting.py:321` and `:429`. The `<4.0` ceiling in
  `pyproject.toml` covers it for now; the replacement is
  `aiohttp.encode_basic_auth()` with an `Authorization` header.
- **A runtime import** in `src/mail_proxy/sql/adapters/postgresql.py:54`
  (`from psycopg_pool import AsyncConnectionPool` inside the method), against the
  project rule that imports live at the top of the file.
- **The fullstack suite is red** on `develop` (52 failed, 116 errors). It
  pre-dates this line — the same file gives an identical result with and without
  the 0.7.7 commits — and it sits outside CI and outside the pre-push gate, being
  the Docker stress suite.

## Two traps worth knowing before touching this

**A `v*` tag publishes a release.** `.github/workflows/publish.yml` builds the
package, pushes it to **PyPI** and creates a GitHub Release;
`.github/workflows/docker.yml` pushes the image to **GHCR**. Both trigger on
`tags: ['v*']`. A version published to PyPI cannot be reused, not even after
deletion. Bookmarks for the experimental line therefore use a `dev/` prefix,
which matches neither workflow: `dev/0.7.6` and `dev/0.7.7` exist and published
nothing.

**A local editable checkout hides upstream breakage.** With genro-asgi installed
editable, the test suite runs against working-tree code that the published wheel
does not carry, so a green local run proves nothing about CI. This has already
produced one false green in this line. A faithful check needs a clean
environment:

```bash
python -m venv /tmp/verify
/tmp/verify/bin/pip install -e ".[dev,postgresql]"
/tmp/verify/bin/python -m pytest tests/ -m "not fullstack and not db"
```

---

**Immediate next step**: read the three genro-asgi documents, then settle
whether the application builds the core (2a) or the core receives the
application (2b). Everything else follows from that call.
