# Spec: Configuration and Defaults

How workstation configuration is stored, how an operation decides what to use, and
what may change a stored default.

## Principle

Configuring is separate from doing. The settings interface exists to change
defaults; everything else — a command, a submission — reads them and leaves them
alone. A user who sends one job to a different farm has not reconfigured their
workstation.

This holds for every interface. A graphical selector is not a licence to persist.

## Storage model

Settings live in one config file, in sections scoped by the context they belong
to: profile-scoped settings nest under the AWS profile, farm-scoped settings
under the farm.

Each context therefore keeps its own values at once. Selecting a different farm
changes which queue is *read*, not which queue is *stored*; returning to the
previous farm restores its queue. Switching context must never write to the
context being left or entered.

A scoped setting's location depends on the current value of its parent, so
establish the parent before writing the child.

## Scoped choices and stored defaults

An operation works on an in-memory copy of the configuration, seeded from the
stored defaults. Options passed to a command, and resources chosen in a
submitter, go there and expire with the operation.

Only the settings interface writes to the file. Its edits are staged and applied
together, so the user can review or abandon them.

The in-memory copy must be detached. Configuration is cached per process, so
mutating the cached instance produces not an override but a pending change, which
the next write to the file will silently carry with it.

## Resolving what to use

1. What this operation already chose.
2. The stored default for the current context, if still available.
3. The only available resource, if exactly one exists.
4. Nothing.

Auto-selecting a sole resource serves the operation in progress: it never
overrides a stored default, and never becomes one. A stored value that cannot be
listed — usable by this user but not enumerable — is still honored.

## Testing

Assert persistence against the config file. Reading a setting back consults the
cache, which cannot distinguish a stored value from a scoped one.

A scoped choice has two halves and both need asserting: the value reached the
operation, and the file did not change.
