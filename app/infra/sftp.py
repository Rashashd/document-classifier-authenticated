"""SFTP poller adapter (paramiko). Not thread-safe — single-threaded use only."""

from __future__ import annotations

import logging
import posixpath
from typing import Iterator

import paramiko

from app.infra.exceptions import SFTPConnectError


logger = logging.getLogger(__name__)

_TIFF_SUFFIXES: tuple[str, ...] = (".tiff", ".tif")


class SFTPClient:
    """Long-lived SFTP session that fetches *.tiff drops and deletes them.

    Lifecycle: construct → ``connect()`` once → call
    ``list_and_download_new_files()`` each polling tick → ``close()``
    on shutdown. Usable as a context manager.
    """

    def __init__(
        self,
        host:     str,
        port:     int,
        username: str,
        password: str,
    ) -> None:
        self._host     = host
        self._port     = port
        self._username = username
        self._password = password
        self._transport: paramiko.Transport | None  = None
        self._sftp:      paramiko.SFTPClient | None = None

    def connect(self) -> None:
        """Open the Transport+SFTPClient session. Idempotent."""
        if self._sftp is not None and self._transport and self._transport.is_active():
            return

        try:
            transport = paramiko.Transport((self._host, self._port))
            transport.connect(username=self._username, password=self._password)
            sftp = paramiko.SFTPClient.from_transport(transport)
        except (paramiko.SSHException, OSError) as exc:
            logger.exception(
                "sftp: connection failed to %s:%d as %r",
                self._host, self._port, self._username,
            )
            raise SFTPConnectError(
                f"could not connect to {self._host}:{self._port} as "
                f"{self._username!r}: {exc}"
            ) from exc

        if sftp is None:
            transport.close()
            raise SFTPConnectError(
                f"SFTP subsystem failed to open against {self._host}:{self._port}"
            )

        self._transport = transport
        self._sftp      = sftp
        logger.info("sftp: connected to %s:%d as %r", self._host, self._port, self._username)

    def close(self) -> None:
        """Tear down the session. Safe to call multiple times."""
        if self._sftp is not None:
            try:
                self._sftp.close()
            except Exception:  # noqa: BLE001
                logger.debug("sftp: error closing SFTPClient", exc_info=True)
            self._sftp = None
        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                logger.debug("sftp: error closing Transport", exc_info=True)
            self._transport = None

    def __enter__(self) -> "SFTPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def list_and_download_new_files(
        self,
        remote_dir: str,
    ) -> Iterator[tuple[str, bytes]]:
        """Yield ``(filename, bytes)`` for each *.tiff in ``remote_dir``.

        Reads each file, deletes it from the server on successful read,
        and yields the payload. Delete-after-read gives at-most-once
        delivery — the downstream inference worker MUST dedupe on
        ``(batch_id, filename)``.

        ``remote_dir`` is the path inside the SFTP session — atmoz/sftp
        chroots ``scanner`` to ``/home/scanner``, so use ``/upload``
        (not ``/home/scanner/upload``).
        """
        if self._sftp is None:
            raise RuntimeError(
                "SFTPClient: connect() must be called before "
                "list_and_download_new_files()."
            )

        try:
            entries = self._sftp.listdir(remote_dir)
        except FileNotFoundError:
            logger.warning("sftp: remote_dir %r not found, skipping tick", remote_dir)
            return

        for name in entries:
            if not name.lower().endswith(_TIFF_SUFFIXES):
                continue

            # posixpath, not os.path: SFTP paths are always POSIX.
            remote_path = posixpath.join(remote_dir, name)

            try:
                with self._sftp.open(remote_path, mode="rb") as handle:
                    handle.set_pipelined(True)
                    data: bytes = handle.read()
            except Exception:  # noqa: BLE001 — polling-tick boundary
                logger.exception(
                    "sftp: failed to read %r, leaving for retry", remote_path
                )
                continue

            try:
                self._sftp.remove(remote_path)
                logger.info("sftp: read+deleted %r (%d bytes)", remote_path, len(data))
            except Exception:  # noqa: BLE001
                # Read succeeded, delete failed: yield anyway so we
                # don't lose data; downstream dedupes.
                logger.exception(
                    "sftp: read %r ok but delete failed; downstream MUST dedupe",
                    remote_path,
                )

            yield name, data
