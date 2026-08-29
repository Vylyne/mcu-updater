"""run_streamed: the plumbing every build log depends on."""

from __future__ import annotations

import sys
import threading
import time

import pytest

from mcu_updater.build import classify_output, run_streamed
from mcu_updater.errors import OperationCancelled

from .conftest import cmd_tokens

CHILD_MANY = "for i in range(5000):\n    print(i)\n"
CHILD_SLOW = (
    "import time\n"
    "print('first', flush=True)\n"
    "time.sleep(1.0)\n"
    "print('last', flush=True)\n"
)
CHILD_FOREVER = (
    "import time\n"
    "i = 0\n"
    "while True:\n"
    "    print(i, flush=True)\n"
    "    i += 1\n"
    "    time.sleep(0.01)\n"
)
CHILD_BOTH_STREAMS = (
    "import sys\n"
    "print('to-stdout', flush=True)\n"
    "print('to-stderr', file=sys.stderr, flush=True)\n"
)


def _collect(lines: list[str]):
    def reporter(stream: str, line: str) -> None:
        if stream == "stdout":
            lines.append(line)

    return reporter


def test_every_line_is_delivered_in_order(tmp_path):
    got: list[str] = []
    rc = run_streamed(
        [sys.executable, "-c", CHILD_MANY],
        cwd=str(tmp_path),
        reporter=_collect(got),
    )
    assert rc == 0
    assert got == [str(i) for i in range(5000)]


def test_output_arrives_incrementally_not_at_exit(tmp_path):
    """A build log that only appears when make finishes is useless."""
    stamps: list[tuple[str, float]] = []

    def reporter(stream: str, line: str) -> None:
        if stream == "stdout":
            stamps.append((line, time.monotonic()))

    run_streamed([sys.executable, "-c", CHILD_SLOW], cwd=str(tmp_path), reporter=reporter)

    assert [s[0] for s in stamps] == ["first", "last"]
    # The child sleeps 1s between them; if we only got output at exit these
    # timestamps would be nearly identical.
    assert stamps[1][1] - stamps[0][1] > 0.5


def test_stderr_is_merged_into_the_stream(tmp_path):
    got: list[str] = []
    run_streamed(
        [sys.executable, "-c", CHILD_BOTH_STREAMS],
        cwd=str(tmp_path),
        reporter=_collect(got),
    )
    assert set(got) == {"to-stdout", "to-stderr"}


def test_nonzero_exit_is_reported(tmp_path):
    rc = run_streamed(
        [sys.executable, "-c", "raise SystemExit(3)"],
        cwd=str(tmp_path),
        reporter=lambda s, line: None,
    )
    assert rc == 3


def test_cancel_terminates_the_child_promptly(tmp_path):
    cancel = threading.Event()
    got: list[str] = []

    def reporter(stream: str, line: str) -> None:
        if stream != "stdout":
            return
        got.append(line)
        if len(got) >= 5:
            cancel.set()

    started = time.monotonic()
    with pytest.raises(OperationCancelled):
        run_streamed(
            [sys.executable, "-c", CHILD_FOREVER],
            cwd=str(tmp_path),
            reporter=reporter,
            cancel=cancel,
        )
    elapsed = time.monotonic() - started
    assert elapsed < 15, f"cancel took {elapsed:.1f}s"
    assert len(got) >= 5


def test_cancel_is_responsive_even_when_the_child_is_silent(tmp_path):
    """The cancel check runs on a timer, not only when a line arrives.

    With a plain `for line in proc.stdout` loop a quiet child could never be
    cancelled at all.
    """
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()

    started = time.monotonic()
    with pytest.raises(OperationCancelled):
        run_streamed(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            cwd=str(tmp_path),
            reporter=lambda s, line: None,
            cancel=cancel,
            poll=0.1,
        )
    assert time.monotonic() - started < 15


def test_dry_run_never_launches_the_command(tmp_path):
    got: list[str] = []
    cmds: list[str] = []

    def reporter(stream: str, line: str) -> None:
        if stream == "stdout":
            got.append(line)
        elif stream == "cmd":
            cmds.append(line)

    marker = tmp_path / "should-not-exist"
    rc = run_streamed(
        [sys.executable, "-c", f"open(r'{marker}', 'w').close()"],
        cwd=str(tmp_path),
        reporter=reporter,
        dry_run=True,
        fake_delay=0.0,
    )
    assert rc == 0
    assert not marker.exists()
    assert len(cmds) == 1
    # A realistic amount of log, so streaming/batching/autoscroll get exercised.
    assert len(got) > 100
    assert any("Linking" in line for line in got)


def test_the_command_is_echoed_before_running(tmp_path):
    cmds: list[str] = []
    run_streamed(
        [sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        reporter=lambda s, line: cmds.append(line) if s == "cmd" else None,
    )
    assert len(cmds) == 1
    assert "-c" in cmd_tokens(cmds[0])


def test_a_missing_executable_is_a_clean_tool_error(tmp_path):
    """A host without build-essential should get a sentence, not a traceback."""
    from mcu_updater.errors import ToolMissingError

    with pytest.raises(ToolMissingError) as exc:
        run_streamed(
            ["definitely-not-a-real-command-xyz"],
            cwd=str(tmp_path),
            reporter=lambda s, line: None,
        )
    assert exc.value.data["tool"] == "definitely-not-a-real-command-xyz"
    assert exc.value.code == "tool_missing"


# classify_output - subprocess-output severity for the joblog UI. Only ever
# applied to the merged stdout+stderr stream, never to the agent's own
# warn/error/info/cmd messages.


def test_classifies_a_compiler_error_line():
    assert classify_output("src/stepper.c:42:5: error: expected ';'") == "stdout_error"


def test_classifies_a_compiler_warning_line():
    assert (
        classify_output("src/stepper.c:42:5: warning: unused variable 'x'")
        == "stdout_warn"
    )


def test_classifies_a_plain_build_line_as_stdout():
    assert classify_output("  Compiling out/src/stepper.o") == "stdout"


def test_werror_flag_does_not_false_positive_as_an_error():
    """`-Werror` contains the substring "error" but not the word "error"."""
    assert classify_output("gcc -Werror -c foo.c") == "stdout"


def test_summary_line_with_zero_counts_does_not_classify_as_an_error():
    """"0 errors, 0 warnings generated" contains "errors"/"warnings" (plural),
    not the singular "error"/"warning" the regex looks for.

    `\\berror\\b` requires a boundary immediately *after* "error" too, and
    the trailing "s" in "errors" is a word character, so there is no
    boundary there - the plural doesn't match. This isn't a workaround; it's
    the word-boundary regex behaving exactly as documented, and it happens to
    keep this common summary-line shape out of the classified streams.
    """
    assert classify_output("0 errors, 0 warnings generated") == "stdout"


def test_make_failure_marker_classifies_as_an_error():
    assert classify_output("make[1]: *** [Makefile:10: all] Error 2") == "stdout_error"


def test_undefined_reference_classifies_as_an_error():
    assert classify_output("undefined reference to `foo'") == "stdout_error"


def test_run_streamed_classifies_subprocess_output_and_preserves_text(tmp_path):
    """classify_output is wired into run_streamed's single reporter call site."""
    got: list[tuple[str, str]] = []
    child = (
        "print('src/a.c: error: boom')\n"
        "print('src/b.c: warning: careful')\n"
        "print('  Compiling out/src/c.o')\n"
    )
    run_streamed(
        [sys.executable, "-c", child],
        cwd=str(tmp_path),
        reporter=lambda stream, line: got.append((stream, line)),
    )
    by_text = {line: stream for stream, line in got}
    assert by_text["src/a.c: error: boom"] == "stdout_error"
    assert by_text["src/b.c: warning: careful"] == "stdout_warn"
    assert by_text["  Compiling out/src/c.o"] == "stdout"


def test_dry_run_log_stays_plain_stdout(tmp_path):
    """_emit_fake_build_log's synthetic output is not classified."""
    got: list[tuple[str, str]] = []
    run_streamed(
        [sys.executable, "-c", "pass"],
        cwd=str(tmp_path),
        reporter=lambda stream, line: got.append((stream, line)),
        dry_run=True,
        fake_delay=0.0,
    )
    streams = {stream for stream, _ in got if stream != "cmd"}
    assert streams == {"stdout"}
