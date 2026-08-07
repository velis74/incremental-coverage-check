"""Shared test utilities."""

from __future__ import annotations

import os
import subprocess
import tempfile

from typing import Dict, Optional

RESOURCES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources")


def resource(name: str) -> str:
    with open(os.path.join(RESOURCES, name), encoding="utf-8") as handle:
        return handle.read()


def resource_path(name: str) -> str:
    return os.path.join(RESOURCES, name)


def write(path: str, content: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


class TemporaryRepository:
    """A throwaway git repository, for tests that need real git behaviour."""

    def __init__(self) -> None:
        self._directory = tempfile.TemporaryDirectory()
        self.path = self._directory.name
        self.git("init", "--initial-branch=main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", self.path, *args], check=True, capture_output=True, text=True).stdout

    def write(self, relative: str, content: str) -> str:
        return write(os.path.join(self.path, relative), content)

    def commit(self, message: str, files: Optional[Dict[str, str]] = None) -> str:
        for relative, content in (files or {}).items():
            self.write(relative, content)
        self.git("add", "-A")
        self.git("commit", "-m", message, "--allow-empty")
        return self.git("rev-parse", "HEAD").strip()

    def cleanup(self) -> None:
        self._directory.cleanup()

    def __enter__(self) -> TemporaryRepository:
        return self

    def __exit__(self, *exc_info) -> None:
        self.cleanup()
