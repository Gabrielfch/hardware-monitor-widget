"""Sign the SHA256SUMS.txt of a DRAFT GitHub release and (optionally) publish it. Maintainer-only.

    python tools/signing/sign_release.py vX.Y.Z [--dist DIR] [--yes] [--key PATH] [--publish] [--attest]

Needs the GitHub CLI `gh` >= 2.49 (for `gh api --slurp` and the optional `gh attestation verify`), logged in with write
access to the repository. Steps:
 0. check the gh version and that the public key derived from the private key is embedded in
    updater.PUBLIC_KEYS (otherwise installed apps would reject the signature: abort before uploading);
 1. download SHA256SUMS.txt, the Setup .exe and the portable .zip of the draft release into a temp folder
    (`gh release download`; if gh cannot find the draft by tag, fall back to `gh api` on the releases list,
    which does include drafts, and download the assets by id);
 2. recompute the SHA-256 of those two files and check them against SHA256SUMS.txt (abort on mismatch);
 2b. compute the SHA-256 of the LOCAL build artifacts in --dist (default ``dist``) and require them to be
    identical to the draft's (the draft is untrusted: whoever can edit it could swap the files); show the
    hashes and ask for interactive confirmation (``--yes`` skips the question) before signing anything;
 3. only with --attest: `gh attestation verify` each file against the repository (build provenance; this
    project builds locally, so attestations exist only if you publish them yourself); abort on failure;
 4. sign SHA256SUMS.txt with the private key and verify the signature with the derived public key;
 5. upload SHA256SUMS.txt.sig (`gh release upload --clobber`);
 6. download SHA256SUMS.txt, the .sig and both files again from the draft and verify signature (against the
    embedded PUBLIC_KEYS, like the app does) and hashes; abort if anything does not match;
 7. with --publish: `gh release edit vX.Y.Z --draft=false`.

The private key is read from the key file (default %USERPROFILE%\\.hmw-signing\\hmw_release_ed25519.key)
and is never printed or logged. Signature format: base64 of the 64 raw bytes, one line.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from hardware_monitor_widget import ed25519, updater  # noqa: E402

_TAG_RE = re.compile(r"\Av(\d{1,4})\.(\d{1,4})\.(\d{1,4})\Z")
_HEX_RE = re.compile(r"\A[0-9a-fA-F]{64}\Z")
_GH_VERSION_RE = re.compile(r"gh version (\d+)\.(\d+)\.(\d+)")
MIN_GH_VERSION = (2, 49, 0)


class SignError(Exception):
    pass


def default_key_path() -> Path:
    import os
    home = os.environ.get("USERPROFILE") or str(Path.home())
    return Path(home) / ".hmw-signing" / "hmw_release_ed25519.key"


def gh(*args: str, binary: bool = False):
    """Run the GitHub CLI and return stdout (str, or bytes with ``binary``).

    On failure raise SignError with its stderr (never anything secret).
    """
    try:
        if binary:
            res = subprocess.run(["gh", *args], capture_output=True, timeout=600)
        else:
            res = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise SignError(f"could not run gh: {exc}") from exc
    if res.returncode != 0:
        err = res.stderr.decode("utf-8", "replace") if isinstance(res.stderr, bytes) else (res.stderr or "")
        raise SignError(f"gh {' '.join(args[:2])} failed (exit {res.returncode}): {err.strip()}")
    out = res.stdout
    if binary:
        return out.encode("utf-8") if isinstance(out, str) else (out or b"")
    return out.decode("utf-8", "replace") if isinstance(out, bytes) else (out or "")


def check_gh_version() -> None:
    out = gh("--version")
    m = _GH_VERSION_RE.search(out)
    if not m:
        raise SignError("could not determine the gh version")
    if tuple(map(int, m.groups())) < MIN_GH_VERSION:
        need = ".".join(map(str, MIN_GH_VERSION))
        raise SignError(f"gh {'.'.join(m.groups())} is too old; gh >= {need} is required")


def read_seed(path: Path) -> bytes:
    try:
        text = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise SignError(f"cannot read the key file {path}: {type(exc).__name__}") from exc
    if not _HEX_RE.match(text):
        raise SignError("the key file does not contain a 64-hex-character seed")
    return bytes.fromhex(text)


def _download_via_api(tag: str, repo: str, dest: Path, names: list[str]) -> None:
    """Fallback: find the release (drafts included) by tag_name in the releases list, fetch assets by id."""
    raw = gh("api", f"repos/{repo}/releases", "--paginate", "--slurp")
    try:
        pages = json.loads(raw)
    except ValueError as exc:
        raise SignError("gh api returned invalid JSON for the releases list") from exc
    releases = []
    for page in pages if isinstance(pages, list) else []:
        releases.extend(page if isinstance(page, list) else [page])
    matches = [r for r in releases if isinstance(r, dict) and r.get("tag_name") == tag]
    if len(matches) != 1:
        raise SignError(f"expected exactly one release with tag {tag}, found {len(matches)}")
    assets = {a.get("name"): a.get("id") for a in matches[0].get("assets") or [] if isinstance(a, dict)}
    for name in names:
        aid = assets.get(name)
        if not isinstance(aid, int):
            raise SignError(f"{name} is not an asset of the release {tag}")
        data = gh("api", "-H", "Accept: application/octet-stream", f"repos/{repo}/releases/assets/{aid}",
                  binary=True)
        (dest / name).write_bytes(data)


def download_assets(tag: str, repo: str, dest: Path, names: list[str]) -> None:
    """Download the named assets of the (draft) release ``tag`` into ``dest``; all must exist afterwards."""
    try:
        gh("release", "download", tag, "--repo", repo, "--dir", str(dest), "--clobber",
           *[a for n in names for a in ("--pattern", n)])
    except SignError as exc:
        print(f"note: gh release download failed ({exc}); falling back to the releases API", file=sys.stderr)
        _download_via_api(tag, repo, dest, names)
    for n in names:
        if not (dest / n).is_file():
            raise SignError(f"{n} was not downloaded from the release")


def verify_attestations(paths: list[Path], repo: str) -> None:
    for p in paths:
        try:
            gh("attestation", "verify", str(p), "--repo", repo)
        except SignError as exc:
            raise SignError(f"build provenance attestation failed for {p.name}: {exc}") from exc


def local_hashes(dist: Path, names: list[str]) -> dict[str, str]:
    """SHA-256 of each named artifact in ``dist``; SignError when one is missing."""
    out = {}
    for n in names:
        f = Path(dist) / n
        if not f.is_file():
            raise SignError(f"local artifact {f} not found: build the release first (or pass --dist)")
        h = hashlib.sha256()
        with open(f, "rb") as fh:
            for block in iter(lambda: fh.read(1024 * 1024), b""):
                h.update(block)
        out[n] = h.hexdigest()
    return out


def sign_release(tag: str, key_path: Path, publish: bool = False, repo: str = updater.REPO,
                 attest: bool = False, dist: Path | str = "dist", assume_yes: bool = False,
                 confirm=None) -> str:
    """Returns the public key (hex) that verified the signature. Raises SignError on any problem."""
    m = _TAG_RE.match(tag)
    if not m:
        raise SignError("tag must look like vX.Y.Z")
    version = ".".join(m.groups())
    exes = [updater.installer_asset_name(version), f"HardwareMonitorWidget-portable-{version}.zip"]
    seed = read_seed(key_path)
    pub = ed25519.public_from_seed(seed)
    if pub.hex() not in {k.lower() for k in updater.PUBLIC_KEYS}:
        raise SignError(f"the public key {pub.hex()} is NOT in updater.PUBLIC_KEYS: installed apps would "
                        "reject the signature; nothing was uploaded")
    local = local_hashes(Path(dist), exes)            # before any network traffic
    check_gh_version()
    names = [updater.SUMS_NAME, *exes]

    with tempfile.TemporaryDirectory(prefix="hmw-sign-") as tmp:
        tmpdir = Path(tmp) / "draft"
        tmpdir.mkdir()
        download_assets(tag, repo, tmpdir, names)
        sums_path = tmpdir / updater.SUMS_NAME
        sums_bytes = sums_path.read_bytes()
        sums = sums_bytes.decode("utf-8", errors="replace")
        for n in exes:
            want = updater.expected_hash(sums, n)
            got = hashlib.sha256((tmpdir / n).read_bytes()).hexdigest()
            if want is None:
                raise SignError(f"{n} is not listed (exactly once) in {updater.SUMS_NAME}")
            if want != got:
                raise SignError(f"hash mismatch for {n}: SHA256SUMS.txt says {want}, the file is {got}")
        for n in exes:
            if local[n] != hashlib.sha256((tmpdir / n).read_bytes()).hexdigest():
                raise SignError(f"the draft's {n} is NOT the file built locally in {dist}; nothing was signed")
        print(f"SHA-256 of the artifacts to sign ({tag}), identical locally and in the draft:")
        for n in exes:
            print(f"  {local[n]}  {n}")
        if not assume_yes:
            try:
                answer = (confirm or input)("Sign these artifacts? Type 'yes' to continue: ")
            except (EOFError, OSError):
                answer = ""
            if str(answer).strip().lower() not in ("y", "yes"):
                raise SignError("aborted: not confirmed; nothing was signed")
        if attest:
            verify_attestations([tmpdir / n for n in exes], repo)

        sig = ed25519.sign(seed, sums_bytes)
        if not ed25519.verify(pub, sums_bytes, sig):
            raise SignError("internal error: the new signature does not verify")
        sig_path = tmpdir / updater.SIG_NAME
        sig_path.write_text(ed25519.encode_signature(sig), encoding="ascii", newline="\n")
        gh("release", "upload", tag, str(sig_path), "--repo", repo, "--clobber")

        # Re-download everything from the draft and check it exactly as an installed app would.
        check = Path(tmp) / "recheck"
        check.mkdir()
        download_assets(tag, repo, check, [*names, updater.SIG_NAME])
        c_sums = (check / updater.SUMS_NAME).read_bytes()
        if not updater.verify_sums_signature(c_sums, (check / updater.SIG_NAME).read_bytes()):
            raise SignError("re-verification failed: the uploaded signature does not verify with the "
                            "embedded PUBLIC_KEYS; the draft was NOT published")
        c_text = c_sums.decode("utf-8", errors="replace")
        for n in exes:
            if not updater.verify_file(check / n, c_text, n):
                raise SignError(f"re-verification failed: {n} does not match the signed SHA256SUMS.txt; "
                                "the draft was NOT published")
        if publish:
            gh("release", "edit", tag, "--repo", repo, "--draft=false")
    return pub.hex()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sign and optionally publish a draft release.")
    ap.add_argument("tag", help="release tag, e.g. v1.4.0")
    ap.add_argument("--key", type=Path, default=None, help="private key file (default: ~/.hmw-signing/...)")
    ap.add_argument("--publish", action="store_true", help="publish the draft after uploading the signature")
    ap.add_argument("--dist", type=Path, default=Path("dist"),
                    help="folder with the locally built artifacts to compare with the draft (default: dist)")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation before signing")
    ap.add_argument("--attest", action="store_true",
                    help="also verify GitHub build-provenance attestations (`gh attestation verify`)")
    args = ap.parse_args(argv)
    try:
        pub = sign_release(args.tag, args.key or default_key_path(), args.publish,
                           attest=args.attest, dist=args.dist, assume_yes=args.yes)
    except SignError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"Signed {updater.SUMS_NAME} of {args.tag}; verified with public key {pub}")
    print("Published." if args.publish else "Uploaded as DRAFT. Publish with --publish when ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
