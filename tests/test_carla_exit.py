"""A demo's process must end with its own exit code and all of its output.

The CARLA client can abort during interpreter shutdown, after the work is done: a day 4 run printed
"result": "success" and then exited 134, which failed a pass of run_carla_all.sh. The demos now skip
shutdown with os._exit. That loses buffered output unless it is flushed first, and the run scripts
read that output to decide what happened, so both are checked here in a real child process.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _run(body: str) -> subprocess.CompletedProcess:
    header = ("import sys\n"
              f"sys.path.insert(0, {str(SRC)!r})\n"
              "from marshal_ai.carla_connection import exit_without_teardown\n")
    return subprocess.run([sys.executable, "-c", header + textwrap.dedent(body)],
                          capture_output=True, text=True, timeout=30)


def test_the_exit_code_is_the_one_main_returned():
    for code in (0, 1, 2):
        assert _run(f"exit_without_teardown({code})").returncode == code


def test_buffered_output_is_not_lost():
    # Piped stdout is block-buffered, so without a flush this line would never arrive.
    done = _run("""
        print('{"type": "day4.done", "result": "success"}')
        print("to stderr", file=sys.stderr)
        exit_without_teardown(0)
    """)

    assert '"result": "success"' in done.stdout
    assert "to stderr" in done.stderr


def test_shutdown_really_is_skipped():
    """The point of it: nothing registered for interpreter shutdown runs."""
    done = _run("""
        import atexit
        atexit.register(lambda: print("shutdown ran"))
        exit_without_teardown(0)
    """)

    assert "shutdown ran" not in done.stdout


def test_every_carla_demo_exits_this_way():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    for name in ("day2", "day4", "day5", "lab", "scene1", "yard"):
        text = (scripts / f"demo_carla_{name}.py").read_text()
        assert "exit_without_teardown(main())" in text, f"demo_carla_{name}.py exits normally"
