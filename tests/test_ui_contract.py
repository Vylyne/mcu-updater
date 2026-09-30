"""The standalone UI's `fw.*` literals against the agent's real registry.

ui/src is TypeScript, hand-mirrored from docs/agent-api.md the same way the
Mainsail panel's types.ts is - see tests/test_agent_methods.py for that half.
Nothing in the JS toolchain checks a method name against the agent that
actually serves it, so a typo'd `fw.staus` would only surface on a printer.
This is pure Python, so it runs in the existing gate on every commit with no
npm involved.
"""

from __future__ import annotations

import pathlib
import re

from mcu_updater import API_VERSION
from mcu_updater.agent.methods import Api

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
UI_SRC = REPO_ROOT / "ui" / "src"

# Matches a quoted `fw.foo.bar` string literal in TypeScript/Vue source.
_FW_METHOD_RE = re.compile(r"""["'](fw\.[a-z_]+(?:\.[a-z_]+)*)["']""")


def _fw_literals_in_ui() -> set[str]:
    found: set[str] = set()
    for path in UI_SRC.rglob("*"):
        if path.suffix not in (".ts", ".vue"):
            continue
        if path.name.endswith(".spec.ts"):
            continue
        text = path.read_text(encoding="utf-8")
        found.update(_FW_METHOD_RE.findall(text))
    return found


def test_every_fw_method_literal_in_the_ui_is_real():
    known = set(Api.METHODS)
    referenced = _fw_literals_in_ui()
    unknown = referenced - known
    assert not unknown, (
        f"ui/src references fw.* method(s) the agent does not have: {sorted(unknown)}. "
        "Check docs/agent-api.md and Api.METHODS in "
        "src/mcu_updater/agent/methods/status.py for the real name."
    )


def test_ui_supported_api_version_matches_the_agent():
    text = (UI_SRC / "api" / "agent.ts").read_text(encoding="utf-8")
    match = re.search(r"SUPPORTED_API_VERSION\s*=\s*(\d+)", text)
    assert match is not None, "ui/src/api/agent.ts must define SUPPORTED_API_VERSION"
    assert int(match.group(1)) == API_VERSION, (
        "ui/src/api/agent.ts's SUPPORTED_API_VERSION has drifted from "
        f"mcu_updater.API_VERSION ({API_VERSION}). A UI that refuses to render "
        "above the wrong ceiling either blocks a compatible agent or renders "
        "against one it cannot actually understand - see docs/agent-api.md's "
        "fw.ping section."
    )


SRC = REPO_ROOT / "src"

_SUBMIT_RE = re.compile(r"""runner\.submit\(\s*["']([a-z_]+)["']""")
_JOB_KIND_RE = re.compile(r"export type JobKind\s*=([^;]+);")
_DEFERRED_RE = re.compile(r"DEFERRED_CANCEL_KINDS[^=]*=\s*new Set\(\[([^\]]*)\]")
_QUOTED_RE = re.compile(r"""["']([a-z_]+)["']""")


def _agent_job_kinds() -> set[str]:
    kinds: set[str] = set()
    for path in SRC.rglob("*.py"):
        kinds |= set(_SUBMIT_RE.findall(path.read_text(encoding="utf-8")))
    return kinds


def _jobs_ts() -> str:
    return (UI_SRC / "api" / "jobs.ts").read_text(encoding="utf-8")


def _ui_job_kinds() -> set[str]:
    match = _JOB_KIND_RE.search(_jobs_ts())
    assert match is not None, "ui/src/api/jobs.ts must define JobKind"
    return set(_QUOTED_RE.findall(match.group(1)))


def _ui_deferred_kinds() -> set[str]:
    match = _DEFERRED_RE.search(_jobs_ts())
    assert match is not None, "ui/src/api/jobs.ts must define DEFERRED_CANCEL_KINDS"
    return set(_QUOTED_RE.findall(match.group(1)))


def test_every_job_kind_the_agent_submits_is_one_the_ui_names():
    """A kind the UI has never heard of falls through `cancelIsImmediate` as
    immediately cancellable. `display_flash` did exactly that, telling the user
    a flash would stop mid-write when the agent was correctly deferring it."""
    agent = _agent_job_kinds()
    assert agent, "the runner.submit scan found nothing; the pattern has rotted"
    assert agent <= _ui_job_kinds(), sorted(agent - _ui_job_kinds())


def test_the_ui_defers_exactly_the_kinds_the_agent_defers():
    from mcu_updater.jobs import IMMEDIATELY_CANCELLABLE

    assert _ui_deferred_kinds() == _ui_job_kinds() - set(IMMEDIATELY_CANCELLABLE)
