# Spec: Configuration and Defaults

How workstation configuration is stored, how a default is chosen, and which
actions may change it.

## Design goals

1. **Persisting a default is a deliberate act.** Changing what the *next*
   command or session does requires the user to go somewhere that means
   "configure me" — the settings interface. Doing work must never silently
   reconfigure the workstation.
2. **Doing work is scoped to the work.** Choosing a farm for one submission, or
   passing an option to one command, affects that operation and nothing else.
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
to, writing a scoped setting while its parent is in flux files it under the
wrong context. Establish the parent first.

## Two states, one rule

Configuration exists as the file on disk and as an in-memory copy belonging to
one operation. A write either targets the persistent config or an explicit
in-memory config, and only the former reaches disk.

An in-memory config is a **detached copy**, never a shared cached instance.
Configuration is cached per process, so mutating the cached instance in place
doesn't create an override — it creates a pending change that any later
persisting write will serialize, turning a scoped choice into a stored default.

## Who may set a default

Only the settings interface — the settings dialog and the equivalent CLI
commands — writes defaults. Its edits are staged and applied together, so the
user can review or abandon them before anything is stored.

Everything else operates on an in-memory copy for the duration of one operation:
options passed to a single command, and resources chosen in the submitter for a
single submission. Both read the stored defaults as their starting point and
leave them untouched.

Both interfaces follow the same rule; a graphical selector is not a licence to
persist. The submitter's farm and queue controls exist so a user can send one
job somewhere else, not to reconfigure the workstation.

## Resolving what to use

An operation resolves each resource in this order:

1. The value already chosen for this operation, if any.
2. The stored default for the current context, if it is still available.
3. The sole available resource, when exactly one exists and nothing is stored.
4. Nothing selected.

Auto-selecting a sole resource is a convenience for the current operation, not a
choice made on the user's behalf: it is never persisted, and it never overrides a
stored default. A stored value that cannot be listed — the user may have
permission to use a resource but not to enumerate its siblings — is still
honored and shown, not discarded.

## Testing this

Assertions about persistence must read the **config file**. Reading a setting
back through the normal accessor consults the in-memory cache, so it cannot
distinguish a value written to disk from one only mutated in memory.

For a scoped choice, assert both halves: the value reached the operation, and the
file did not change.
