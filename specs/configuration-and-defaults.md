# Spec: Configuration and Defaults

How workstation configuration is stored, how defaults are chosen, and which
actions are allowed to change them.

## Design goals

1. **Persisting a default is a deliberate act.** Changing what the *next*
   command does requires the user to say so — by editing settings, or by
   picking a resource in the submitter. Passing a flag to one command must not
   silently reconfigure the workstation.
2. **A default is the last thing you chose, and it should feel that way.** Users
   don't set defaults up front; they pick a farm and expect it to still be there
   tomorrow.
3. **Switching context is non-destructive.** Moving between AWS profiles or
   farms surfaces that context's own values. It must never overwrite the values
   belonging to the context being left or entered.

## Storage model

Settings live in a single config file, in sections scoped by the context they
belong to. Scoping is hierarchical: profile-scoped settings nest under the AWS
profile, farm-scoped settings under the farm, and so on.

The practical consequence — and the reason the nesting exists — is that each
context keeps its own values simultaneously. Selecting a different farm changes
which queue is *read*, not which queue is *stored*. Returning to a previous farm
restores its queue.

Because a setting's location depends on the current value of what it's scoped
to, writing a scoped setting while its parent is in flux will file it under the
wrong context. Establish the parent first.

## Two states, one rule

Configuration exists as the file on disk and as an in-memory copy for the
current command. The single rule that separates them: a write either targets the
persistent config or an explicit in-memory config, and only the former reaches
disk.

An in-memory config must be a **detached copy**, never the shared cached
instance. The cache is process-wide, so mutating it in place doesn't create an
override — it creates a pending change that any later persisting write will
serialize, turning a one-command flag into a permanent default.

## Who may set a default

| Actor | Persists? |
|---|---|
| Explicit settings change (CLI `config set`, settings UI) | Yes — this is the point |
| Picking a resource in the submitter | Yes — selection *is* the act of choosing |
| A CLI flag on any command | **No** — scoped to that invocation |
| Auto-selection of a sole available resource | CLI: no. GUI: yes |

The GUI asymmetry is intentional. A selector reflects a persistent choice, so
resolving it must leave that choice recorded; a flag is an instruction about one
command.

## Resolving what to use

Both surfaces resolve in the same order:

1. The stored value for the current context, if it is still available.
2. The sole available resource, when exactly one exists and nothing is stored.
3. Nothing selected.

Auto-selection never overrides a stored value — it only fills a gap. A stored
value that can't be listed (the user may use a resource but not enumerate its
siblings) is still honored and shown, not discarded.

## Region

Region is discovered per call, not remembered. Resources are located across
regions and carry their own region; an unset region means "let the SDK decide."

A region flag scopes one command. It is not written to config, because region is
farm-scoped: an implicit write would stamp a transient value onto whichever farm
happened to be current — possibly a farm the flag never referred to. Users who
want a persistent region set it explicitly like any other default.

## Testing this

Assertions about persistence must read the **config file**. Reading a setting
back through the normal accessor consults the in-memory cache, so it cannot
distinguish "written to disk" from "mutated in memory" — the exact confusion
this design guards against, and one that has produced false-passing tests.

For a per-invocation override, assert two things: the value reached the command,
and the file did not change.
