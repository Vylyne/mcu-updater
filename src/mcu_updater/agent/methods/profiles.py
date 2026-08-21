"""fw.profile.* -- seeding and forgetting vendor/custom answer sets."""

from __future__ import annotations

from typing import Any

from ... import firmware, profiles
from ...config import Registry
from ...errors import (
    UpdaterError,
)
from ..rpc import ERR_INVALID_PARAMS, RpcError
from ._api import _Base


class ProfilesMixin(_Base):
    def profile_list(self, args: dict) -> dict[str, Any]:
        """What a type could be seeded from, and what it currently is.

        Keyed on the type rather than on a firmware family, because "which
        profiles apply to this board" is the question a panel is actually
        asking - and the answer depends on which family the type declares it
        runs, not on which trees happen to be installed.

        Each entry carries the answers that **distinguish** it from the others.
        Cartographer's USB and CAN variants differ by one answer out of seven, so
        a picker listing all seven under each of eight entries hides the one line
        that decides anything. That comparison is text over eight small files, so
        it is free and unconditional.

        `detail: true` additionally labels those answers with the tree's own
        prompt text - "Use PA11/PA12 for CANbus" rather than
        `STM32_CANBUS_PA11_PA12`. That needs the Kconfig tree, so it is opt-in
        and costs one parse: affordable because opening a picker is a click, in
        the same budget `fw.kconfig.open` already spends, and deliberately kept
        off `fw.status`, which every state event recomputes for every client.
        """
        name = self._require_str(args, "name")
        reg = self.registry()
        mcu = reg.get(name)
        families = firmware.load(self.paths)
        fw = str(args.get("fw") or mcu.application(families)).strip()
        if fw not in families and fw not in firmware.BUILTIN:
            raise RpcError(
                f"'fw' must be one of {', '.join(self._fw_names())}", ERR_INVALID_PARAMS
            )

        seeds = profiles.available(self.paths, fw, families, mcu_type=name)
        differences = profiles.distinguishing(seeds)
        labels = (
            self._prompt_labels(fw, families, differences)
            if bool(args.get("detail"))
            else {}
        )

        return {
            "type": name,
            "firmware": mcu.application(families),
            "fw": fw,
            "profile": mcu.profile,
            "available": [
                {
                    **seed.to_json(),
                    "distinguishing": [
                        {**row, "label": labels.get(str(row["symbol"]))}
                        for row in differences.get(seed.name, [])
                    ],
                }
                for seed in seeds
            ],
            "state": {
                f: profiles.status(self.paths, name, f, families).to_json()
                for f in mcu.families()
            },
        }

    def _prompt_labels(
        self,
        fw: str,
        families: dict[str, firmware.FirmwareFamily],
        differences: dict[str, list[dict[str, Any]]],
    ) -> dict[str, str]:
        """One parse, labelling every symbol every profile is told apart by.

        Degrades to no labels rather than failing the listing: a tree that cannot
        be parsed - not cloned, or missing its vendored kconfiglib - is a picker
        showing raw symbol names, which is worse than prompt text and far better
        than an error where the profiles should be.
        """
        from ...providers import kconfig as kconfig_mod

        symbols = {str(row["symbol"]) for rows in differences.values() for row in rows}
        if not symbols:
            return {}
        fw_dir = firmware.resolve(self.paths, fw, families).source_dir(self.paths)
        try:
            return kconfig_mod.prompts(fw_dir, sorted(symbols))
        except (UpdaterError, OSError):
            return {}

    def profile_apply(self, args: dict) -> dict[str, Any]:
        """Seed a type's answers from its firmware tree, bootloader included.

        The bootloader is derived by default rather than on request. Seeding
        only the application leaves a type whose two configs describe different
        boards, and the pair only has to disagree about one address for the
        result to be a board that does not come back - so the safe combination
        is the one that takes no extra argument.

        `derive` is still separable, because a type with no bootloader
        (`katapult_installed: false`) has nothing to derive and asking for it
        would be an error rather than a no-op.

        **A job, not a synchronous answer.** Seeding parses a Kconfig tree up to
        three times - the seed, a bare probe of the bootloader tree, then the
        carried answers - and one parse is a few hundred milliseconds on a Pi.
        Moonraker awaits our reply with no timeout, so a method that might sit
        past a second holds a browser's HTTP request open; the rule at the top
        of this file exists for exactly that. Every argument is still validated
        *before* the job exists, so a typo is refused immediately rather than
        arriving as a job that dies a second later.
        """
        runner = self._require_runner()
        name = self._require_str(args, "name")
        profile = self._require_str(args, "profile")
        force = bool(args.get("force"))

        reg = self.registry()
        mcu = reg.get(name)
        families = firmware.load(self.paths)
        fw = str(args.get("fw") or mcu.application(families)).strip()
        if fw not in families and fw not in firmware.BUILTIN:
            raise RpcError(
                f"'fw' must be one of {', '.join(self._fw_names())}", ERR_INVALID_PARAMS
            )

        # The family this type actually carries a bootloader for, if any -
        # falling back to "katapult" only if derive is forced true on a type
        # that declares none, which lets that fail downstream exactly as it
        # would have before this had a name other than "katapult" to try.
        boot_fw = mcu.bootloader(families) or "katapult"
        derive = args.get("derive")
        derive = (mcu.bootloader(families) is not None) if derive is None else bool(derive)
        # Named before the job starts, so an unknown profile is a refusal rather
        # than a failed job - and so the confirmation can say what it will write.
        seed = profiles.find(self.paths, fw, profile, families, mcu_type=name)
        # Likewise "that config is yours, pass force": a refusal a caller can
        # act on, not a failure it has to read out of a dead job. Two file
        # hashes and no Kconfig parse, so it is fine to ask here and again
        # inside the write, where it is the authority.
        for family in [fw] + ([boot_fw] if derive else []):
            profiles.refuse_if_customised(self.paths, name, family, force=force)

        def run(ctx) -> dict[str, Any]:
            steps = 2 if derive else 1
            ctx.step(f"Seeding {name} ({fw}) from {seed.name}", 0, steps)
            applied = profiles.apply_seed(
                self.paths, name, fw, seed.name, families=families, force=force
            )
            for line in applied.answers:
                ctx.reporter("stdout", line)
            out: dict[str, Any] = {"applied": applied.to_json(), "derived": None}

            if derive:
                # Not wrapped in a try: a bootloader that cannot be derived is a
                # board that should not be flashed, and reporting the application
                # seeding as a success with a warning attached is how that gets
                # missed. The application's config stays - it is valid on its own.
                ctx.step(f"Deriving {boot_fw} from {fw}", 1, steps)
                derived = profiles.derive_bootloader(
                    self.paths, name, fw, boot_fw, families=families, force=force
                )
                for line in derived.dropped:
                    ctx.reporter("info", f"{boot_fw} does not define {line} - dropped")
                out["derived"] = derived.to_json()

            # The intent goes in the hand-edited config; the verdict stays in the
            # data tree. Only for the application - katapult's is always derived,
            # so recording a second key would be restating that.
            if fw == mcu.application(families):
                with Registry.mutate(self.paths, f"profile for {name}") as writable:
                    writable.get(name).profile = applied.profile

            ctx.step(f"Seeded {name}", steps, steps)
            self._changed()
            return out

        job = runner.submit(
            "profile_apply", {"name": name, "fw": fw, "profile": seed.name}, run
        )
        return {"job_id": job.id, "job": job.to_dict(), "type": name, "fw": fw}

    def profile_forget(self, args: dict) -> dict[str, Any]:
        """Detach a type from its profile, leaving every answer exactly as is.

        The escape hatch that makes the drift reporting tolerable: someone who
        has deliberately customised a config can say so once, instead of
        reading "Customised" as a warning for the life of the install.
        """
        name = self._require_str(args, "name")
        reg = self.registry()
        mcu = reg.get(name)
        fw = str(args.get("fw") or "").strip()
        targets = [fw] if fw else list(mcu.families())

        forgotten = [f for f in targets if profiles.forget(self.paths, name, f)]
        if not fw or mcu.application() in targets:
            with Registry.mutate(self.paths, f"forget profile for {name}") as writable:
                writable.get(name).profile = ""

        self._changed()
        return {"type": name, "forgotten": forgotten}

    # -- dispatch ----------------------------------------------------------
