"""Where updates come from and which keys sign them.

Fill ``PUBLIC_KEYS`` with the output of ``tools/signing/gen_keys.py`` (two keys: primary + offline backup)
and rebuild. While it is empty the updater is DISABLED and never touches the network.
Only public keys belong here: the private seeds live outside the repository.
"""
from __future__ import annotations

REPO = "Gabrielfch/hardware-monitor-widget"

# Ed25519 public keys (64 hex chars each) accepted for release signatures. Several keys allow rotation:
# ship the new key in a release signed with the old one, later drop the old one.
PUBLIC_KEYS: tuple[str, ...] = ()


def is_configured() -> bool:
    """A repository and at least one well-formed public key are embedded."""
    if not REPO or "/" not in REPO:
        return False
    for k in PUBLIC_KEYS:
        try:
            if len(bytes.fromhex(k)) == 32:
                return True
        except (ValueError, TypeError):
            continue
    return False
