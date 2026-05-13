"""Drop four files onto the SFTP server to exercise the ingest triage paths.

The four payloads correspond to the four triage outcomes:

* ``empty_noise.tiff``       — 0 bytes        (empty → delete)
* ``honest_mistake.csv``     — text/csv       (wrong extension → delete)
* ``malicious_payload.tiff`` — fake TIFF      (bad magic → quarantine)
* ``valid_document.tiff``    — real TIFF      (happy path)

The integration test ``tests/integration/test_ingest_pipeline.py``
imports :func:`drop_files` and :func:`get_test_drops` from this module.
Run from the host as a script for ad-hoc manual testing::

    docker compose up -d sftp
    uv run python scripts/generate_test_drops.py
"""

from __future__ import annotations

import io

from PIL import Image

from app.infra.sftp import SFTPClient


SFTP_HOST:  str = "localhost"
SFTP_PORT:  int = 2222
SFTP_USER:  str = "scanner"
SFTP_PASS:  str = "password123"
REMOTE_DIR: str = "/upload"


def _build_valid_tiff() -> bytes:
    """A genuine 10x10 grayscale TIFF for the happy-path case."""
    img = Image.new("L", (10, 10), color=128)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def get_test_drops() -> dict[str, bytes]:
    """Return the canonical {filename: bytes} mapping for the four drops."""
    return {
        "empty_noise.tiff":       b"",
        "honest_mistake.csv":     b"col_a,col_b\n1,2\n",
        "malicious_payload.tiff": b"this is not a tiff, it just claims to be",
        "valid_document.tiff":    _build_valid_tiff(),
    }


def drop_files(
    sftp:       SFTPClient,
    remote_dir: str = REMOTE_DIR,
) -> list[str]:
    """Write all four test drops via an already-connected ``SFTPClient``.

    Returns the list of filenames dropped (basenames, not absolute paths).
    """
    drops = get_test_drops()
    for name, data in drops.items():
        sftp.write_file(f"{remote_dir}/{name}", data)
    return list(drops)


def main() -> None:
    with SFTPClient(host=SFTP_HOST, port=SFTP_PORT, username=SFTP_USER, password=SFTP_PASS) as sftp:
        names = drop_files(sftp, REMOTE_DIR)
    for name in names:
        print(f"dropped {REMOTE_DIR}/{name}")


if __name__ == "__main__":
    main()
