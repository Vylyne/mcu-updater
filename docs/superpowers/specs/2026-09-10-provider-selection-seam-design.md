# Provider selection as a seam

> **Superseded in part.** The section "Type-level flash stays refused,
> honestly" below, and the plan's Global Constraint "No type-level CMake
> flash", read a gap as a boundary. They are withdrawn by
> `2026-09-11-cmake-provenance-design.md`. What shipped here is still
> correct for the backend as it stands; the refusal is removed by that
> spec's work, not by reverting this one.

## Purpose

Two seams already exist and each deliberately excludes the same third thing.
`providers/spec.py` says "**Flashing is not here.** A provider answers questions
about files we produce; writing them to hardware is a different axis."
`flashers/__init__.py` says "why selection is not part of it." Both exclusions
are correct in isolation. Together they leave *selection* - given a type name,
which build system and which flasher own it - with no home, so each call site
answers it privately.

Four call sites answer it today. Two ask a resolver; two guess:

| Site | How it answers |
|---|---|
| `agent/methods/build.py:34` | `_provider_of`, the real resolver |
| `agent/methods/flash.py:49` | `_provider_of` |
| `agent/methods/bulk.py:477` | `only in reg.names() or only in self.pio_types()` |
| `cli.py:707` | `args.type not in reg.names()` implies PlatformIO |

The two guesses encode a two-provider world that stopped being true when CMake
landed. `_provider_of`'s own docstring already states the intent they violate:
resolution happens "once, rather than at every call site that would otherwise
branch."

This is not the `[mcu]`-registry episode repeating. There, `build_all` walked
the registry "because that was the only list it had" - no seam existed. Here the
seam exists and half the call sites reach it. This spec finishes that migration.

## What selection answers

Selection maps a configured type name to the provider that owns it. It is a
pure function of `Paths`: every input is re-read from configuration on each
call, because a type added over `fw.type.add` must be answerable without
restarting the agent.

It answers exactly one question and raises rather than guessing. A name
belonging to no provider is a typo or a deleted section; defaulting it to
kconfig produces "no saved klipper config" for a screen, which is a confusing
way to learn the name was wrong.

Order is load-bearing and is preserved from `_provider_of`: PlatformIO, then
CMake, then the kconfig registry. A CMake type is kept out of the registry by
`config.py`'s foreign-builder rule, so the order does not matter today - but if
that rule ever slipped, asking the registry first would answer `kconfig_make`
for a Roadrunner and send it down a build path with no `.config` to run,
silently. Asking in this order cannot.

## Where selection lives

Selection lives beside the provider seam it resolves into, reachable by both
the agent and the CLI. It takes `Paths` and a name; it returns a provider name.
It raises a transport-neutral error carrying the unknown name and the set of
configured names.

The agent's `_provider_of` becomes a thin adapter over it, translating that
error into the `RpcError` shape callers already receive. The CLI calls the same
function and renders the same error as CLI output. Neither reimplements the
lookup, and the wire payload for an unknown type does not change.

## Identity is not build semantics

`Registry.resolve_serial` resolves a serial within the kconfig registry.
`Registry.resolve_declared_serial` resolves one across every declared type
regardless of builder, and its docstring already draws the line: "provider
registries own build semantics, while the shared document owns the configured
serial-to-type pairing."

A flash path resolves *identity*, so it uses the declared resolver. The CLI's
flash path uses `resolve_serial` today, which is why a Roadrunner serial the
system does track reports "isn't tracked under any MCU type."

## Type-level flash stays refused, honestly

`fw.flash_all` cannot flash a CMake type: `bulk.py` enumerates boards from the
kconfig registry and has no CMake branch. `status.py`'s `_cmake_target`
correspondingly never calls `_flash_actions`, so the panel shows no type-level
Flash button.

That absence is currently *correct* and must stay correct. This spec does not
add type-level CMake flashing; it makes the refusal honest. `_require_flashable_type`
stops guessing and consults selection, and a CMake name is refused with a reason
that says type-level flash is not available for this builder and that per-device
flash is - instead of today's `unknown_type`, which claims the type does not
exist.

Adding the button before the backend can serve it would be the failure this spec
exists to prevent. The button is added in the same change that teaches `bulk.py`
to enumerate CMake boards, and not before.

## Out of scope

**`scope: "stale"` semantics for CMake.** Skipping boards already running a
build requires version comparison against declared serials, and the BOOTSEL
write is sequential and service-stopping in a way the bulk path does not model.
That is its own design, with its own spec.

**Type-level CMake flash** in `bulk.py`, `cli.py`, or the panel. Deferred with
the above, for the same reason.

**The flasher and provider seams themselves.** Neither changes. This spec adds
the resolver they both assume and neither owns.

## Errors and reporting

An unknown type name raises one error type carrying the name and the configured
names, translated at each boundary: `RpcError` with the registry's existing
`unknown_type` payload for the agent, a message on stderr with a nonzero exit
for the CLI. The payload shape does not change.

A CMake name reaching a type-level flash path is refused with a distinct reason
naming the builder and pointing at per-device flash. It is never silently
enumerated to an empty board list, which would report success having flashed
nothing.

A serial that resolves under a declared type is flashed through that type's
provider. A serial tracked nowhere keeps the existing unknown-serial error; a
serial tracked under several keeps the existing ambiguity error and its
disambiguation hint.

## Tests and documentation

Tests cover: selection resolving each of the three providers and raising on an
unknown name; ordering (a name in both PlatformIO and the registry resolves to
PlatformIO, and a CMake name never resolves to `kconfig_make`); the agent
adapter preserving the existing `unknown_type` wire payload; `_require_flashable_type`
refusing a CMake name with the builder-specific reason rather than `unknown_type`;
the CLI flashing a CMake serial per device with and without `-t`; and the CLI
raising no `KeyError` for a CMake name under any argument combination.

`AGENTS.md` and the README `## Features` list record that provider selection is
a seam, and that type-level flash remains unavailable for CMake types.
