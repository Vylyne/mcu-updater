"""fw.build and fw.kconfig.* -- compiling firmware and editing saved answers."""

from __future__ import annotations

import os
from typing import Any

from ... import firmware, profiles, providers
from ...errors import (
    UpdaterError,
)
from ...lock import exclusive
from ..rpc import ERR_INVALID_PARAMS, ERR_METHOD_NOT_FOUND, RpcError
from ._api import _Base


class BuildMixin(_Base):
    #: Declared explicitly, not left to the `_Api` Protocol: assigning
    #: `self._kconfig_sessions` in `_sessions` below makes mypy treat this
    #: class as the attribute's owner and infer its type from that assignment
    #: alone, which is circular against the `if ... is None:` guard reading it
    #: first. An explicit annotation here breaks the cycle; `StatusMixin.__init__`
    #: still does the real (lazy - `None` until first use) initialisation.
    _kconfig_sessions: Any | None

    def _require_runner(self):
        if self.runner is None:
            raise RpcError(
                "this agent is running read-only; no job runner is available",
                ERR_METHOD_NOT_FOUND,
            )
        return self.runner

    def _provider_of(self, name: str) -> str:
        """Which build system owns this type, by name.

        The whole reason `fw.display.build` existed as a separate method: the
        caller had to know which kind of thing it was addressing, so the panel
        carried a `kind` and picked a method from it. It does not have to. A type
        name resolves to exactly one provider, and this is where that happens -
        once, rather than at every call site that would otherwise branch.

        Raises rather than guessing. A name belonging to neither is a typo or a
        section somebody deleted, and defaulting it to kconfig would produce
        "no saved klipper config" for a screen.
        """
        if name in self.pio_types():
            return providers.PlatformIO.name
        # Before the registry, not after. A cmake type is kept out of it by
        # `config.py`'s foreign-builder rule, so the order does not matter
        # today - but if that ever slipped, asking the registry first would
        # answer `kconfig_make` for a Roadrunner and send it down a build path
        # that has no .config to run, silently. Asking here first cannot.
        if name in self._cmake_types():
            return providers.Cmake.name
        if name in self.registry().names():
            return providers.KconfigMake.name
        raise RpcError(
            f"no type '{name}' is configured.",
            data={
                "code": "unknown_type",
                "message": "no such type",
                "data": {
                    "name": name,
                    "known": sorted(
                        set(self.registry().names())
                        | set(self.pio_types())
                        | set(self._cmake_types())
                    ),
                },
            },
        )

    def _cmake_types(self) -> dict:
        """Configured types whose declared family is cmake-built.

        Mirrors `pio_types()`, and read the same way: from the config each
        time, because a type added over `fw.type.add` has to be answerable
        without restarting the agent.
        """
        from ...providers import cmake as cmake_mod

        return cmake_mod.load(self.paths)

    def build(self, args: dict) -> dict[str, Any]:
        """Start a build. Returns a job id immediately - never blocks.

        Every type, whichever build system compiles it. A PlatformIO type has no
        family - its env already names the board, the partitions and the flags -
        so `fw` is not merely optional there, it is meaningless, and passing one
        is a caller still thinking in kinds.
        """
        runner = self._require_runner()
        name = args.get("name")
        if name:
            owner = self._provider_of(str(name))
            if owner == providers.PlatformIO.name:
                return self._pio_build(args)
            if owner == providers.Cmake.name:
                return self._cmake_build(args)

        fw = args.get("fw")
        known = self._fw_names()
        if not name or fw not in known:
            raise RpcError(
                f"'name' is required and 'fw' must be one of {list(known)}",
                ERR_INVALID_PARAMS,
            )
        name, fw = str(name), str(fw)

        reg = self.registry()
        reg.get(name)  # fail fast on an unknown type, before creating a job
        if not os.path.exists(self.paths.config_file(name, fw)):
            # menuconfig is ncurses and cannot run here. Say so precisely rather
            # than starting a job that dies immediately.
            raise RpcError(
                f"{name} has no saved {fw} config. Run "
                f"'updatefw menuconfig -t {name} -f {fw}' over SSH once first.",
                data={
                    "code": "no_saved_config",
                    "message": "menuconfig has never been run for this type",
                    "data": {"type": name, "fw": fw},
                },
            )

        jobs = args.get("jobs")
        clean = args.get("clean")
        # Tri-state. Absent means "whatever `reseed_on_build` says", which is how
        # this call, the CLI and a fleet build end up doing the same thing; the
        # dialog sends an explicit answer when it has asked the user for one.
        reseed = args.get("reseed")

        def run(ctx) -> dict[str, Any]:
            from ...build import build as do_build

            ctx.step(f"Building {fw} for {name}", 0, 1)
            result = do_build(
                self.paths,
                self.registry(),
                self.settings(),
                name,
                fw,
                reporter=ctx.reporter,
                cancel=ctx.cancel,
                jobs=int(jobs) if jobs is not None else None,
                clean=bool(clean) if clean is not None else None,
                reseed=bool(reseed) if reseed is not None else None,
            )
            ctx.step(f"Built {fw} for {name}", 1, 1)
            return {
                "type": name,
                "fw": fw,
                "bin_path": result.bin_path,
                "uf2_path": result.uf2_path,
                "duration": round(result.duration, 2),
                "fw_sha": result.fw_sha,
                "config_rewritten": result.config_rewritten,
                # Which profile was taken before building, if one was. Null on
                # every build that did not reseed, including one that was willing
                # to and found nothing to take.
                "reseeded": result.reseeded,
            }

        job = runner.submit("build", {"name": name, "fw": fw}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _pio_build(self, args: dict) -> dict[str, Any]:
        """Compile one PlatformIO env. Touches no hardware."""
        runner = self._require_runner()
        name = self._require_str(args, "name")
        types = self.pio_types()
        if name not in types:
            raise RpcError(
                f"no PlatformIO type '{name}' is configured.",
                data={
                    "code": "unknown_type",
                    "message": "no such type",
                    "data": {"name": name, "known": sorted(types)},
                },
            )
        display = types[name]

        def run(ctx) -> dict[str, Any]:
            from ...providers import pio as pio_mod

            ctx.step(f"Building {display.env}", 0, 1)
            path = pio_mod.build(
                self.paths, self.settings(), display, reporter=ctx.reporter, cancel=ctx.cancel
            )
            ctx.step(f"Built {display.env}", 1, 1)
            return {"name": name, "env": display.env, "firmware": path}

        job = runner.submit("display_build", {"name": name}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def clean(self, args: dict) -> dict[str, Any]:
        """Delete a type's generated build directory. Synchronous, no job.

        Not a job: it is an `rmtree` of one directory, so the runner's
        machinery would outweigh the work and a progress bar for it would be a
        lie. It does take the exclusive lock, for the one way this could do
        damage - removing a build directory out from under a compile using it -
        which is why `fw.roadrunner.*` takes it for its own synchronous work.

        Answers `removed: null` rather than failing for a build system that
        keeps no such directory, so a panel can offer the action on any type
        and get a true answer instead of having to know which providers have
        one.
        """
        name = self._require_str(args, "name")
        owner = self._provider_of(name)
        if owner != providers.Cmake.name:
            return {"name": name, "provider": owner, "removed": None}

        from ...providers import cmake as cmake_mod

        entry = self._cmake_types()[name]
        with exclusive(self.paths, f"clean {name}"):
            removed = cmake_mod.clean_build_dir(entry.source)
        return {"name": name, "provider": owner, "removed": removed}

    def _cmake_build(self, args: dict) -> dict[str, Any]:
        """Compile one cmake target and stage its image. Touches no hardware.

        The module function rather than the provider adapter, exactly as
        `_pio_build` does: the adapter returns nothing, and the staged path is
        what a caller wants back from a build it asked for by name.
        """
        runner = self._require_runner()
        name = self._require_str(args, "name")
        types = self._cmake_types()
        if name not in types:
            raise RpcError(
                f"no cmake type '{name}' is configured.",
                data={
                    "code": "unknown_type",
                    "message": "no such type",
                    "data": {"name": name, "known": sorted(types)},
                },
            )
        entry = types[name]

        # Refuse synchronously, as the CLI's cmake branch does, rather than
        # submitting a job that will fail. `build()` refuses authoritatively on
        # its own - it will not stage an image from a target this tree no
        # longer declares - but that answer arrives inside a job, as a failed
        # build somebody has to go and read. This is the readable one.
        #
        # `source_problem()` rather than `Cmake().blocked()`: they return the
        # identical string here (`blocked()`'s only other branch is the missing
        # entry, and `name` was just looked up in `types`), and reaching the
        # adapter would mean building an `Install`, which parses the config a
        # third time in this method and would make a malformed `[mcu ...]` or
        # PlatformIO section refuse a cmake build that has nothing to do with
        # either.
        from ...providers import cmake as cmake_mod

        problem = cmake_mod.source_problem(entry)
        if problem:
            raise RpcError(
                problem,
                data={
                    "code": "build_blocked",
                    "message": "this type cannot be built here",
                    "data": {"name": name, "fw": entry.firmware, "reason": problem},
                },
            )

        def run(ctx) -> dict[str, Any]:
            ctx.step(f"Building {entry.cmake_target}", 0, 1)
            path = cmake_mod.build(
                self.paths, self.settings(), entry, reporter=ctx.reporter, cancel=ctx.cancel
            )
            ctx.step(f"Built {entry.cmake_target}", 1, 1)
            return {
                # Both keys, deliberately. `type` is what every other job of
                # kind `build` carries, and reusing the kind without it would
                # leave a client reading `result.type` with `undefined`;
                # `name` is what `_pio_build` returns and what this method was
                # called with.
                "type": name,
                "name": name,
                "fw": entry.firmware,
                "cmake_target": entry.cmake_target,
                "uf2_path": path,
            }

        # Kind `build`, not a new one. It is a compile like any other - so it
        # is immediately cancellable (jobs.py's IMMEDIATELY_CANCELLABLE) and a
        # client already knows how to render it, where an unheard-of kind would
        # be a wire surprise for nothing.
        job = runner.submit("build", {"name": name, "fw": entry.firmware}, run)
        return {"job_id": job.id, "job": job.to_dict()}

    def _sessions(self) -> Any:
        """The session store, created on first use.

        Lazily, because an agent that never opens a config should not pay for the
        import or hold the state.
        """
        if self._kconfig_sessions is None:
            from ...providers.kconfig import SessionStore

            store: Any = SessionStore(self.paths)
            self._kconfig_sessions = store
        return self._kconfig_sessions

    def kconfig_available(
        self, families: dict[str, firmware.FirmwareFamily] | None = None
    ) -> dict[str, bool]:
        """Which firmware trees can be configured from here.

        A stat per tree, so it is cheap enough for fw.status. Lets the panel hide
        the button rather than offer one that fails on a host where the source tree
        is missing.

        `families` is accepted so a caller already holding the parsed sections -
        `firmware_families`, on the same status call - does not re-read the
        config file to learn what it already knows.
        """
        from ...providers.kconfig import kconfiglib_path

        if families is None:
            families = firmware.load(self.paths)

        out = {}
        for fw in firmware.names_of(families):
            fw_dir = firmware.resolve(self.paths, fw, families).source_dir(self.paths)
            out[fw] = os.path.isfile(kconfiglib_path(fw_dir)) and os.path.isfile(
                os.path.join(fw_dir, "src", "Kconfig")
            )
        return out

    def _session(self, args: dict) -> Any:
        return self._sessions().get(self._require_str(args, "session"))

    def kconfig_open(self, args: dict) -> dict[str, Any]:
        """Parse a firmware tree and start a configuration session.

        The one method here that can approach a second: a full Klipper Kconfig
        parse is a few hundred milliseconds on a Pi. Every other kconfig call works
        against the tree this leaves in memory, which is the reason sessions exist
        at all.
        """
        name = self._require_str(args, "name")
        fw = self._require_str(args, "fw")
        known = self._fw_names()
        if fw not in known:
            raise RpcError(f"'fw' must be one of {', '.join(known)}", ERR_INVALID_PARAMS)

        # The type has to exist: the answers are saved per type, and inventing a
        # directory for a typo is not a helpful thing to do.
        mcu = self.registry().get(name)

        store = self._sessions()
        if not bool(args.get("force")):
            clash = store.dirty_for(name, fw)
            if clash is not None:
                raise RpcError(
                    f"another session ({clash.id}) has unsaved changes to "
                    f"{name}/{fw}. Opening a second one risks one save discarding "
                    f"the other's work - finish or discard that one first, or pass "
                    f"force to take it over.",
                    data={
                        "code": "kconfig_session_conflict",
                        "message": "another session has unsaved changes",
                        "data": {"session": clash.id, "type": name, "fw": fw},
                    },
                )

        session = store.open(name, fw, chipset=mcu.chipset)
        with session.lock:
            payload = session.menu()
        payload["available"] = self.kconfig_available()
        # Only ever non-empty on the open that produced it - a fresh session
        # with no saved config, pre-set from this type's own recorded
        # chipset. Lets the panel say where the defaults came from instead of
        # presenting them as if the user had already chosen them.
        payload["seeded"] = session.seeded
        return payload

    def kconfig_menu(self, args: dict) -> dict[str, Any]:
        """Re-read the current screen, for a client that lost its copy."""
        session = self._session(args)
        with session.lock:
            return session.menu()

    def kconfig_enter(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        node_id = self._require_str(args, "id")
        with session.lock:
            return session.enter(node_id)

    def kconfig_up(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        with session.lock:
            return session.up()

    def kconfig_set(self, args: dict) -> dict[str, Any]:
        """Assign one symbol and return the menu it leaves behind."""
        session = self._session(args)
        node_id = self._require_str(args, "id")
        if "value" not in args:
            raise RpcError("'value' is required", ERR_INVALID_PARAMS)
        with session.lock:
            return session.set_value(node_id, str(args.get("value")))

    def kconfig_help(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        node_id = self._require_str(args, "id")
        with session.lock:
            return session.help(node_id)

    def kconfig_search(self, args: dict) -> dict[str, Any]:
        session = self._session(args)
        with session.lock:
            return session.search(str(args.get("query") or ""))

    def kconfig_reset(self, args: dict) -> dict[str, Any]:
        """Discard unsaved edits by reparsing from disk."""
        session = self._session(args)
        with session.lock:
            return session.reset()

    def kconfig_save(self, args: dict) -> dict[str, Any]:
        """Write the answers, optionally kicking off a build.

        Takes the *build* lock, not the registry one, because this genuinely
        conflicts with a build: `build()` hashes the .config to record what a binary
        was compiled from, so changing it underneath would leave provenance that
        does not match the artifact - and staleness would then report a wrong
        binary as fresh.

        The save is also captured as this type's **own profile**. It is the
        moment a user stops tracking the vendor's answers and starts keeping
        their own, and it is nearly free here because the tree is parsed and the
        minimal answers come back from the save itself. Without it, editing a
        profile stays the dead end it is today: the drift is reported, and the
        answers that caused it have nowhere to live.
        """
        from ...lock import ExclusiveLock

        session = self._session(args)
        want_build = bool(args.get("build"))

        with session.lock:
            lock = ExclusiveLock(self.paths)
            try:
                lock.acquire(f"save config {session.mcu_type}/{session.fw}")
            except UpdaterError:
                raise
            try:
                result = session.save()
                result["custom_profile"] = self._capture_answers(
                    session.mcu_type, session.fw, result.get("answers") or []
                )
            finally:
                lock.release()
            result["menu"] = session.menu()

        self._changed()

        if want_build:
            # Deliberately after the lock is released: build() takes it itself, and
            # holding it across both would deadlock.
            started = self.build({"name": session.mcu_type, "fw": session.fw})
            result["job_id"] = started.get("job_id")
        return result

    def _capture_answers(
        self, mcu_type: str, fw: str, answers: list[str]
    ) -> str | None:
        """Keep a just-saved set of answers as this type's own profile.

        Skipped where it would only make noise: a tree that ships no profiles has
        no picker to offer this in, and a type that has never been near one has
        nothing to fork from - for those, the `.config` is already the whole
        story and a second copy of it under a profile name is a file nobody asked
        for.

        Best effort. The answers are not at risk if this fails - they are in the
        `.config` that was just written, which is what the capture is a copy of.
        Failing the save over a bookkeeping write would be the tail wagging the
        dog, so the result reports null and the caller can see it did not happen.
        """
        if not answers:
            return None
        try:
            families = firmware.load(self.paths)
            # Read after the write, so `customised` here means "this save changed
            # something". A save that changed nothing leaves a config still
            # matching what its profile wrote, and copying that under the user's
            # name would put a duplicate of the vendor's entry in the picker.
            state = profiles.status(self.paths, mcu_type, fw, families)
            if state.managed and state.reason != profiles.CUSTOMISED:
                return None
            if not state.managed and not profiles.available(
                self.paths, fw, families, mcu_type=mcu_type
            ):
                return None
            kept = profiles.capture_custom(
                self.paths,
                mcu_type,
                fw,
                answers=answers,
                parent=state.profile,
                families=families,
            )
        except (UpdaterError, OSError):
            return None
        return kept.name

    def kconfig_close(self, args: dict) -> dict[str, Any]:
        session_id = self._require_str(args, "session")
        return {"session": session_id, "closed": self._sessions().close(session_id)}

    # -- profiles ----------------------------------------------------------
