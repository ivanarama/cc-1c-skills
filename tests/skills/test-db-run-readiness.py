#!/usr/bin/env python3
"""Focused regression tests for the db-run Python readiness gate."""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".claude" / "skills" / "db-run" / "scripts" / "db-run.py"


def load_db_run():
    spec = importlib.util.spec_from_file_location("db_run", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    db_run = load_db_run()

    assert db_run._window_is_ready("1C:Enterprise", True)
    assert not db_run._window_is_ready("1C:Enterprise", False)
    assert not db_run._window_is_ready(
        "Загрузка конфигурационной информации", True
    )
    assert db_run._window_is_ready(
        "Загрузка конфигурационной информации", True, reject_loading_title=False
    )

    calls = []

    def responsive_probe(*args):
        calls.append(args)
        return 1

    assert db_run._window_responds(responsive_probe, 123, timeout_ms=250)
    assert len(calls) == 1
    hwnd, message, wparam, lparam, flags, timeout_ms, result_ptr = calls[0]
    assert (hwnd, message, wparam, lparam) == (123, 0, 0, 0)
    assert flags == 0x0001 | 0x0002
    assert timeout_ms == 250
    assert result_ptr is not None

    assert not db_run._window_responds(lambda *_: 0, 123)

    responses = {101: 0, 202: 1}

    def multi_window_probe(hwnd, *_):
        return responses[hwnd]

    # A first enumerated hung window must not hide a later ready main window.
    assert db_run._select_window_state(
        [(101, "Hung startup window"), (202, "1C:Enterprise")],
        multi_window_probe,
    ) == (202, "1C:Enterprise", True)

    # The same applies when the first window has the known loading title.
    responses[101] = 1
    assert db_run._select_window_state(
        [
            (101, "Загрузка конфигурационной информации"),
            (202, "1C:Enterprise"),
        ],
        multi_window_probe,
    ) == (202, "1C:Enterprise", True)

    # With no ready candidate, retain the normal title for grace diagnostics.
    responses[101] = 1
    responses[202] = 0
    assert db_run._select_window_state(
        [
            (101, "Загрузка конфигурационной информации"),
            (202, "Hung application window"),
        ],
        multi_window_probe,
    ) == (202, "Hung application window", False)
    print("db-run Python readiness: OK")


if __name__ == "__main__":
    main()
