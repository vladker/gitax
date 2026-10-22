# -*- coding: utf-8 -*-
"""Tests for session persistence and the getpass-based prompt helper."""

import json
from unittest.mock import patch

from backuper import Backuper
from utils import prompt_secret


def _write_session(path, payload):
    session_file = path / "backuper_session.json"
    session_file.write_text(json.dumps(payload), encoding="utf-8")
    return session_file


class TestSessionPassword:
    def _session_payload(self, tmp_path, **extra):
        src = tmp_path / "src"
        src.mkdir()
        payload = {"source_path": str(src), "file_list": ["a.txt"]}
        payload.update(extra)
        return payload

    def test_legacy_password_is_scrubbed(self, tmp_path):
        session_file = _write_session(tmp_path, self._session_payload(
            tmp_path, use_password=True, password="legacy-secret"
        ))
        bu = Backuper.__new__(Backuper)
        with patch("backuper.SESSION_FILE", str(session_file)):
            data = bu._load_session()

        assert "password" not in data
        assert data["use_password"] is True
        raw = session_file.read_text(encoding="utf-8")
        assert "legacy-secret" not in raw
        assert json.loads(raw)["use_password"] is True

    def test_session_without_password_untouched(self, tmp_path):
        session_file = _write_session(tmp_path, self._session_payload(tmp_path))
        bu = Backuper.__new__(Backuper)
        with patch("backuper.SESSION_FILE", str(session_file)):
            data = bu._load_session()

        assert data["file_list"] == ["a.txt"]
        assert "password" not in data

    def test_saved_session_round_trip_has_no_password(self, tmp_path):
        session_file = tmp_path / "backuper_session.json"
        bu = Backuper.__new__(Backuper)
        with patch("backuper.SESSION_FILE", str(session_file)):
            bu._save_session({"source_path": str(tmp_path), "file_list": ["a"],
                              "use_password": True})
            assert "password" not in json.loads(session_file.read_text(encoding="utf-8"))


class TestPromptSecret:
    def test_non_tty_falls_back_to_input(self):
        class _Stdin:
            def isatty(self):
                return False

        with patch("sys.stdin", _Stdin()), patch("builtins.input", return_value="  secret  ") as inp:
            assert prompt_secret("Password:") == "secret"
        assert inp.call_args[0][0] == "Password: "

    def test_tty_uses_getpass(self):
        class _Stdin:
            def isatty(self):
                return True

        with patch("sys.stdin", _Stdin()), patch("utils.getpass.getpass", return_value="topsecret\n") as gp:
            assert prompt_secret("Password: ") == "topsecret"
        assert gp.call_args[0][0] == "Password: "

    def test_cancel_returns_empty(self):
        class _Stdin:
            def isatty(self):
                return False

        with patch("sys.stdin", _Stdin()), patch("builtins.input", side_effect=EOFError):
            assert prompt_secret("Password: ") == ""
