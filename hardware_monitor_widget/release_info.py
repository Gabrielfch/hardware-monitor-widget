"""Where updates come from and which keys sign them.

Fill ``PUBLIC_KEYS`` with the output of ``tools/signing/gen_keys.py`` (two keys: primary + offline backup)
and rebuild. While it is empty the updater is DISABLED and never touches the network.
Only public keys belong here: the private seeds live outside the repository.
"""
from __future__ import annotations

REPO = "Gabrielfch/hardware-monitor-widget"

# Ed25519 public keys (64 hex chars each) accepted for release signatures. Several keys allow rotation:
# ship the new key in a release signed with the old one, later drop the old one.
PUBLIC_KEYS: tuple[str, ...] = (
    "4cdc37840feebaf6532e9ebd04a834f101ef2f07c45cd80210da3d55d144a6c0",  # primary
    "03f6202f4e73f11e5ea82b63f00f350b84e499d86eb505c01b3d32a793bc9f4c",  # backup (kept offline)
)


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
