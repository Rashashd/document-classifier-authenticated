"""SFTP adapter (paramiko). Not thread-safe — single-threaded use only."""

from __future__ import annotations

import logging
import posixpath
from typing import Iterator

import paramiko

from app.infra.exceptions import SFTPConnectError


logger = logging.getLogger(__name__)

_TIFF_SUFFIXES: tuple[str, ...] = (".tiff", ".tif")


class SFTPClient:
    """Long-lived SFTP session exposing the primitives the ingest worker needs.

    Lifecycle: construct → ``connect()`` once → call any primitive
    per polling tick → ``close()`` on shutdown. Usable as a context
    manager.
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

    # -- session lifecycle ----------------------------------------------------

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

    # -- primitives -----------------------------------------------------------

    def list_dir(self, remote_dir: str) -> list[str]:
        """Basenames in ``remote_dir``. Empty list if the directory is missing."""
        self._require_session()
        try:
            return self._sftp.listdir(remote_dir)  # type: ignore[union-attr]
        except FileNotFoundError:
            logger.warning("sftp: remote_dir %r not found", remote_dir)
            return []

    def size_of(self, remote_path: str) -> int:
        """Return the size of ``remote_path`` in bytes."""
        self._require_session()
        return self._sftp.stat(remote_path).st_size  # type: ignore[union-attr,return-value]

    def read_partial(self, remote_path: str, n_bytes: int) -> bytes:
        """Return the first ``n_bytes`` of ``remote_path``."""
        self._require_session()
        with self._sftp.open(remote_path, mode="rb") as handle:  # type: ignore[union-attr]
            return handle.read(n_bytes)

    def read_file(self, remote_path: str) -> bytes:
        """Return the full bytes of ``remote_path``."""
        self._require_session()
        with self._sftp.open(remote_path, mode="rb") as handle:  # type: ignore[union-attr]
            handle.set_pipelined(True)
            return handle.read()

    def delete_file(self, remote_path: str) -> None:
        """Remove ``remote_path`` from the server."""
        self._require_session()
        self._sftp.remove(remote_path)  # type: ignore[union-attr]

    def write_file(self, remote_path: str, data: bytes) -> None:
        """Write ``data`` to ``remote_path``, overwriting if it exists."""
        self._require_session()
        with self._sftp.open(remote_path, mode="wb") as handle:  # type: ignore[union-attr]
            handle.write(data)

    def move_file(self, src: str, dest: str) -> None:
        """Move ``src`` to ``dest``. Same filesystem only (uses SFTP rename)."""
        self._require_session()
        self._sftp.rename(src, dest)  # type: ignore[union-attr]

    # -- convenience (legacy, kept for the existing integration test) ---------

    def list_and_download_new_files(
        self,
        remote_dir: str,
    ) -> Iterator[tuple[str, bytes]]:
        """Yield ``(filename, bytes)`` for each *.tiff in ``remote_dir``.

        Reads then deletes; downstream MUST dedupe on filename
        (at-most-once delivery).

        ``remote_dir`` is the in-session path — atmoz/sftp chroots
        ``scanner`` so the share appears at ``/upload``, not
        ``/home/scanner/upload``.
        """
        for name in self.list_dir(remote_dir):
            if not name.lower().endswith(_TIFF_SUFFIXES):
                continue

            remote_path = posixpath.join(remote_dir, name)

            try:
                data = self.read_file(remote_path)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "sftp: failed to read %r, leaving for retry", remote_path
                )
                continue

            try:
                self.delete_file(remote_path)
                logger.info("sftp: read+deleted %r (%d bytes)", remote_path, len(data))
            except Exception:  # noqa: BLE001
                logger.exception(
                    "sftp: read %r ok but delete failed; downstream MUST dedupe",
                    remote_path,
                )

            yield name, data

    # -- internal -------------------------------------------------------------

    def _require_session(self) -> None:
        if self._sftp is None:
            raise RuntimeError("SFTPClient: connect() must be called first.")
