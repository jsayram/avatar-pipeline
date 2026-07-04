import os
import time

import pytest

from lib.approval_lock import ApprovalLockHeldError, release, try_claim


def test_approval_lock_is_exclusive(tmp_path):
    try_claim(tmp_path, "abc123", "dashboard")

    with pytest.raises(ApprovalLockHeldError, match="dashboard"):
        try_claim(tmp_path, "abc123", "telegram")

    release(tmp_path, "abc123")
    try_claim(tmp_path, "abc123", "telegram")


def test_approval_lock_steals_stale_lock(tmp_path):
    try_claim(tmp_path, "abc123", "dashboard")
    path = tmp_path / "abc123" / ".approval_action.lock"
    old = time.time() - (3 * 60 * 60)
    os.utime(path, (old, old))

    try_claim(tmp_path, "abc123", "telegram")

    assert path.read_text() == "telegram"
