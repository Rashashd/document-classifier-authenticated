"""Drop four files onto the local SFTP server to exercise the ingest triage paths.

Run from the host with the compose stack up::

    docker compose up -d sftp
    uv run python scripts/generate_test_drops.py

Files dropped into ``/upload``:

* empty_noise.tiff       — 0 bytes (triage: empty → delete)
* honest_mistake.csv     — text/csv  (triage: wrong extension → delete)
* malicious_payload.tiff — fake TIFF (triage: bad magic → quarantine)
* valid_document.tiff    — real TIFF (happy path)
"""

from __future__ import annotations

import io

import paramiko
from PIL import Image


SFTP_HOST: str = "localhost"
SFTP_PORT: int = 2222
SFTP_USER: str = "scanner"
SFTP_PASS: str = "password123"
REMOTE_DIR: str = "/upload"


def _build_valid_tiff() -> bytes:
    """A genuine 10x10 grayscale TIFF for the happy-path test."""
    img = Image.new("L", (10, 10), color=128)
    buf = io.BytesIO()
    img.save(buf, format="TIFF")
    return buf.getvalue()


def main() -> None:
    drops: dict[str, bytes] = {
        "empty_noise.tiff":       b"",
        "honest_mistake.csv":     b"col_a,col_b\n1,2\n",
        "malicious_payload.tiff": b"this is not a tiff, it just claims to be",
        "valid_document.tiff":    _build_valid_tiff(),
    }

    transport = paramiko.Transport((SFTP_HOST, SFTP_PORT))
    transport.connect(username=SFTP_USER, password=SFTP_PASS)
    sftp = paramiko.SFTPClient.from_transport(transport)
    if sftp is None:
        transport.close()
        raise RuntimeError("could not open SFTP subsystem")

    try:
        for name, data in drops.items():
            remote_path = f"{REMOTE_DIR}/{name}"
            with sftp.open(remote_path, "wb") as fh:
                fh.write(data)
            print(f"dropped {remote_path} ({len(data)} bytes)")
    finally:
        sftp.close()
        transport.close()


if __name__ == "__main__":
    main()
