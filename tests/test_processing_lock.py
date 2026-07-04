"""Exclusive lock guarding the pre-pending-record window for texted-in links."""
import time

import pytest

from lib.processing_lock import LockHeldError, release, try_acquire


def test_acquire_then_release_allows_reacquire(tmp_path):
    try_acquire(tmp_path, "abc123")
    release(tmp_path)
    try_acquire(tmp_path, "def456")  # must not raise


def test_second_acquire_while_held_raises(tmp_path):
    try_acquire(tmp_path, "abc123")
    with pytest.raises(LockHeldError, match="abc123"):
        try_acquire(tmp_path, "def456")


def test_stale_lock_is_stolen(tmp_path, monkeypatch):
    try_acquire(tmp_path, "abc123")
    # simulate an old, abandoned lock by back-dating its mtime
    lock_path = tmp_path / ".link_processing.lock"
    old_time = time.time() - 999999
    import os
    os.utime(lock_path, (old_time, old_time))

    try_acquire(tmp_path, "def456")  # must not raise — steals the stale lock
    assert lock_path.read_text().strip() == "def456"


def test_release_on_nonexistent_lock_is_a_noop(tmp_path):
    release(tmp_path)  # must not raise
