"""Admit a bounded data-only YAML file before Harbor transfers it."""

import json
import os
import stat
import sys
from pathlib import Path

import yaml

LIMIT = 1024 * 1024


def read_submission(directory: Path) -> bytes:
    """Read through directory descriptors so candidate path replacement cannot redirect reads."""
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        if os.listdir(directory_fd) != ["config.yaml"]:
            raise ValueError("Submission must contain only config.yaml")
        fd = os.open(
            "config.yaml",
            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o111 or info.st_nlink != 1:
                raise ValueError("Submission must be a regular, non-executable, unlinked YAML file")
            content = stream.read(LIMIT + 1)
    finally:
        os.close(directory_fd)
    if len(content) > LIMIT:
        raise ValueError("Submission exceeds the YAML size limit")
    if any(isinstance(token, yaml.tokens.AliasToken) for token in yaml.scan(content)):
        raise ValueError("Submission aliases are not admitted")
    value = yaml.safe_load(content)
    if not isinstance(value, dict):
        raise ValueError("Submission must be a YAML mapping")
    # Recursive aliases and Python-specific YAML values are not workflow data.
    json.dumps(value, allow_nan=False)
    return content


def collect(source: Path, destination: Path) -> None:
    """Publish only validated bytes to a root-owned directory the author cannot modify."""
    content = read_submission(source)
    with (destination / "config.yaml").open("xb") as stream:
        stream.write(content)
    (destination / "config.yaml").chmod(0o600)


if __name__ == "__main__":
    if sys.argv[1:] == ["collect"]:
        try:
            collect(Path("/app/submission"), Path("/submission"))
        except OSError, ValueError, TypeError, yaml.YAMLError, RecursionError:
            with Path("/submission/rejection.json").open("x") as stream:
                json.dump(
                    {"status": "rejected", "reason": "unsafe_or_missing_submission"},
                    stream,
                )
    elif sys.argv[1:] == ["verify"]:
        if any(Path("/logs/artifacts").iterdir()):
            raise ValueError("Undeclared conventional artifacts reached the verifier")
        if Path("/submission/config.yaml").exists():
            read_submission(Path("/submission"))
    else:
        raise SystemExit("Expected collect or verify")
