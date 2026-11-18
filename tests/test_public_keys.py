"""Guard the embedded release-signing public keys against typos: a bad key silently disables updates.

An empty tuple is valid while the maintainer has not generated keys yet (updater disabled, no network).
"""
import re

from hardware_monitor_widget import ed25519, release_info, updater


def test_embedded_public_keys_are_valid_and_distinct():
    keys = release_info.PUBLIC_KEYS
    assert isinstance(keys, tuple)
    for k in keys:
        assert re.fullmatch(r"[0-9a-f]{64}", k), "every PUBLIC_KEYS entry must be 64 lowercase hex chars"
    usable = updater.usable_public_keys()
    assert len(usable) == len(keys), "every PUBLIC_KEYS entry must be 32 bytes of hex"
    assert len(set(usable)) == len(usable), "duplicate public key"
    for k in usable:
        assert ed25519._decompress(k) is not None, f"{k.hex()} is not a valid Ed25519 point"


def test_updater_uses_release_info_keys_and_repo():
    assert updater.PUBLIC_KEYS == release_info.PUBLIC_KEYS
    assert updater.REPO == release_info.REPO


def test_empty_keys_mean_updater_disabled():
    if not release_info.PUBLIC_KEYS:
        assert not release_info.is_configured()
        assert not updater.is_enabled()


def test_repo_format():
    assert re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", release_info.REPO)
