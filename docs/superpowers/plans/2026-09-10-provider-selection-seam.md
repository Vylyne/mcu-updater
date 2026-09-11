# Provider selection seam Implementation Plan

> **Superseded in part.** The section "Type-level flash stays refused,
> honestly" below, and the plan's Global Constraint "No type-level CMake
> flash", read a gap as a boundary. They are withdrawn by
> `2026-09-11-cmake-provenance-design.md`. What shipped here is still
> correct for the backend as it stands; the refusal is removed by that
> spec's work, not by reverting this one.

Spec: `docs/superpowers/specs/2026-09-10-provider-selection-seam-design.md`

Finishes a migration that stopped half-done. `_provider_of` resolves a type name
to its provider and says in its own docstring that this happens "once, rather
than at every call site that would otherwise branch." Two call sites use it.
Two guess, both encoding a two-provider world that CMake ended: `bulk.py:477`
and `cli.py:707`. The guesses produce a `KeyError: 'roadrunner'` traceback from
the CLI, a misleading `unknown_type` from `fw.flash_all`, and a Roadrunner
serial the system tracks reporting "isn't tracked under any MCU type."

The fix is small because the pieces exist. The three dependencies of
`_provider_of` are all pure `Paths` lookups - `Registry.load(paths)`,
`pio_mod.load(paths)`, `cmake_mod.load(paths)` - so it lifts out of the agent
unchanged. Only its `RpcError` raise ties it there.

## Global Constraints

- **Behavior-preserving for kconfig and PlatformIO.** No existing resolution
  outcome changes for either. The `unknown_type` wire payload is identical
  before and after.
- **Resolution order is PlatformIO, CMake, registry** - preserved verbatim from
  `_provider_of`, including its comment explaining why the registry is asked
  last.
- **No type-level CMake flash.** Not in `bulk.py`, not in `cli.py`, not in the
  panel. Refusals become honest; they do not become permissions. Adding
  `_flash_actions` to `_cmake_target` is out of scope and stays out.
- **Selection is a pure function of `Paths`**, re-reading config each call. No
  caching, no agent state, no module-level memoization.
- **The provider and flasher seams do not change.** No edits to the
  `providers/spec.py` or `flashers/spec.py` protocols.
- Every task ends with the full gate green: `pytest`, `ruff`, `mypy`, all-LF,
  `git diff --check`.

## File structure

| File | Change |
|---|---|
| `src/mcu_updater/providers/selection.py` | NEW - `provider_of(paths, name)` and `UnknownTypeError` |
| `src/mcu_updater/providers/__init__.py` | export the two new names |
| `src/mcu_updater/agent/methods/build.py` | `_provider_of` becomes an adapter; `_cmake_types` unchanged |
| `src/mcu_updater/agent/methods/bulk.py` | `_require_flashable_type` consults selection |
| `src/mcu_updater/cli.py` | flash path routes by provider; declared-serial resolution |
| `tests/test_provider_selection.py` | NEW - selection unit tests |
| `tests/test_bulk.py` | CMake refusal reason |
| `tests/test_cli.py` | CMake flash routing, no `KeyError` |
| `AGENTS.md`, `README.md` | record the seam and the standing CMake limitation |

## Task 1: Lift selection into a shared seam

Create `src/mcu_updater/providers/selection.py` with `provider_of(paths, name)`
returning a provider name, and `UnknownTypeError` carrying `name` and the
configured names. Move the body of `agent/methods/build.py:34` verbatim -
including the comment explaining why the registry is asked last - replacing
`self.pio_types()`, `self._cmake_types()` and `self.registry()` with the
`Paths`-based loads they already delegate to, and the `RpcError` raise with
`UnknownTypeError`.

Export both from `providers/__init__.py`.

Rewrite the agent `_provider_of` as an adapter: call `provider_of(self.paths,
name)`, catch `UnknownTypeError`, re-raise as the `RpcError` it raises today
with the identical `data` payload. `_cmake_types()` stays where it is - it has
other callers.

This task changes no behavior. Its test is that the existing suite passes
untouched, plus `tests/test_provider_selection.py` covering: each of the three
providers resolving; an unknown name raising; a name present in both PlatformIO
and the registry resolving to PlatformIO; and a CMake name never resolving to
`kconfig_make`.

**Acceptance:** full gate green. The adapter and its two callers are unchanged
in shape. No test asserts a changed `unknown_type` payload.

## Task 2: fw.flash_all refuses CMake honestly

`_require_flashable_type` (`bulk.py:470`) currently reads:

```python
if only in reg.names() or only in self.pio_types():
    return only
reg.get(only)  # raises with the registry's own unknown_type payload
```

Replace the guess with `provider_of`. A PlatformIO or kconfig name returns as
today. A CMake name raises an `RpcError` whose reason says type-level flash is
not available for CMake-built types and that per-device flash is - naming the
type. An unknown name keeps the existing `unknown_type` payload, reached now
through `UnknownTypeError`.

Do not add CMake enumeration to `_boards_to_flash`. Accepting the name without
it would return an empty board list and report a successful flash of nothing -
strictly worse than today's error.

`status.py` is not touched. `_cmake_target` still omits `_flash_actions`, and
that omission is still correct: it accurately reflects a backend that refuses.

**Acceptance:** full gate green. A test asserts a CMake name yields the
builder-specific reason and not `unknown_type`; a test asserts an unknown name
still yields `unknown_type` with its original payload; a test asserts
`fw.flash_all` for a CMake type never returns a job that flashed zero boards
while reporting success.

## Task 3: The CLI routes by provider

Three sites in `cli.py`, all in the flash path:

1. **`flash_fw_cmd:707`** - `if args.type and args.type not in reg.names():`
   routes anything outside the kconfig registry to `_pio_targets` and
   `_ports_free`, where `displays[name]` raises `KeyError: 'roadrunner'`.
   Replace with a `provider_of` dispatch: PlatformIO keeps the existing branch,
   kconfig keeps the existing path, CMake gets its own.
2. **Serial resolution at `:739` and `:753`** - both call `reg.resolve_serial`,
   which knows only the kconfig registry. A flash path resolves identity, so
   both become `reg.resolve_declared_serial` (`config.py:679`), whose docstring
   already names the distinction. The unknown, ambiguous and tracked-elsewhere
   errors and their hints are preserved.
3. **The type-only branch at `:718`** - `reg.get(args.type)` for a CMake name.
   Refuse with the same builder-specific reason Task 2 gives `fw.flash_all`,
   worded for the CLI, pointing at `-s <serial>`.

The CMake single-device branch mirrors `_cmake_flash`
(`agent/methods/flash.py:200-292`): resolve the type, build its flash target
through the flasher seam, and stop services via `stop_services.for_cmake`
(`stop_services.py:79`) rather than `for_display`. `_ports_free` is a
PlatformIO helper and is not reused for CMake - it loads pio displays by name,
which is the KeyError.

Do not add a `-t`-only CMake flash. That is the deferred type-level work.

**Acceptance:** full gate green. Tests assert: `flash -s <cmake serial>`
resolves and routes to the helper flasher; the same with `-t <cmake type>`;
`flash -t <cmake type>` alone gives the builder-specific refusal and exit 1;
and no argument combination involving a CMake name raises `KeyError`. The
traceback from the bench report is the regression test - reproduce it first,
watch it fail, then fix.

## Task 4: Record the seam, and review

Update `AGENTS.md` to state that provider selection is a seam at
`providers/selection.py`, that call sites resolve through it rather than
branching on registry membership, and that type-level flash is unavailable for
CMake types by design pending its own spec. Add the corresponding README
`## Features` entries, and a `## TODO` line for the deferred `scope: "stale"`
work.

Then an independent whole-branch review against the spec, and the full gate.

**Acceptance:** review clean or findings adjudicated; gate green; no membership
test stands in for provider resolution in `cli.py` or `bulk.py`.
