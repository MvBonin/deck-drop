"""single_instance helpers."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from deckdrop.single_instance import (
    is_deckdrop_process,
    pid_file_path,
    stop_other_instances,
    write_pid_file,
)


def test_is_deckdrop_process_matches_cmdline():
    with patch(
        "deckdrop.single_instance._read_cmdline",
        return_value=".venv/bin/deckdrop --headless",
    ):
        assert is_deckdrop_process(1234, skip_pid=9999) is True


def test_is_deckdrop_process_skips_self():
    my_pid = os.getpid()
    assert is_deckdrop_process(my_pid, skip_pid=my_pid) is False


def test_stop_other_instances_honours_skip_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DECKDROP_SKIP_SINGLE_INSTANCE", "1")
    config = tmp_path / "config.toml"
    config.write_text("")
    write_pid_file(config, pid=424242)

    with patch("deckdrop.single_instance.kill_process") as mock_kill:
        stopped = stop_other_instances(7373, config_path=config)
        assert stopped == []
        mock_kill.assert_not_called()


def test_stop_other_instances_kills_pid_from_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DECKDROP_SKIP_SINGLE_INSTANCE", raising=False)
    config = tmp_path / "config.toml"
    config.write_text("")
    write_pid_file(config, pid=55555)

    with (
        patch("deckdrop.single_instance.is_deckdrop_process", return_value=True),
        patch("deckdrop.single_instance.pids_listening_on_port", return_value=[]),
        patch("deckdrop.single_instance.kill_process") as mock_kill,
        patch("deckdrop.single_instance.os.getpid", return_value=99999),
    ):
        stopped = stop_other_instances(7373, config_path=config, my_pid=99999)

    assert stopped == [55555]
    mock_kill.assert_called_once_with(55555)


def test_pid_file_path_next_to_config():
    assert pid_file_path(Path("/home/u/.config/deckdrop/config.toml")) == Path(
        "/home/u/.config/deckdrop/deckdrop.pid"
    )
