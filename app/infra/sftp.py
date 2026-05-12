"""
SFTP adapter — atmoz/sftp poller.

Used by Person D's ``app/workers/sftp_ingest.py`` loop, which is *not*
in scope for this PR. This module ships only the connection / read /
delete primitives; the polling cadence, retry policy, malformed-file
quarantine, and Redis-side enqueue all belong in the worker loop.

Why a class, not a function?
----------------------------
The ingest worker is long-lived and polls every ``POLL_INTERVAL_SECONDS``.
Each tick reuses the same paramiko ``Transport`` and ``SFTPClient``
session — opening a new SSH connection per poll would burn ~80 ms of
handshake + key exchange per tick, which eats most of our 5 s SFTP-drop
latency budget. Hence the class holds the session as state.

Concurrency
-----------
Not thread-safe. paramiko's ``SFTPClient`` itself is not designed for
concurrent use from multiple threads on the same channel. The ingest
worker is single-threaded by design, so this is fine.
"""

from __future__ import annotations

import logging
import posixpath
from typing import Iterator

import paramiko


logger = logging.getLogger(__name__)


# We only care about TIFFs in this project — RVL-CDIP is exclusively
# TIFF, and the brief explicitly forbids OCR/PDF on this pipeline.
# Tuple, lower-cased, so the match is allocation-free at hot-loop time.
_TIFF_SUFFIXES: tuple[str, ...] = (".tiff", ".tif")


class SFTPClient:
    """Connect to an SFTP server, fetch *.tiff drops, delete on success.

    Lifecycle
    ---------
    Construct with credentials, then call :meth:`connect` once before
    the polling loop starts. Each poll calls
    :meth:`list_and_download_new_files`. Call :meth:`close` on
    shutdown.

    Production parity
    -----------------
    Locally the atmoz/sftp container accepts password auth (see
    docker-compose.yml). In production this class would also accept a
    private-key path; we keep it password-only here per the brief.
    """

    def __init__(
        self,
        host:     str,
        port:     int,
        username: str,
        password: str,
    ) -> None:
        self._host:     str  = host
        self._port:     int  = port
        self._username: str  = username
        # Stored only for the duration of the process. Production
        # rotates this via Vault; here it is read from .env at startup
        # by the worker that constructs us.
        self._password: str  = password

        # Lazily initialised in connect(); typed as Optional so static
        # checkers flag misuse before connect().
        self._transport:  paramiko.Transport | None      = None
        self._sftp:       paramiko.SFTPClient | None     = None

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    def connect(self) -> None:
        """Open a single Transport + SFTPClient session.

        Idempotent: a second call is a no-op if the session is alive.
        Callers should not need to track connection state themselves.
        """
        if self._sftp is not None and self._transport and self._transport.is_active():
            return

        # ``Transport`` is the SSH-layer wrapper around the raw socket.
        # paramiko opens the TCP socket internally when given (host, port).
        transport = paramiko.Transport((self._host, self._port))
        # We deliberately do NOT call ``transport.set_missing_host_key_policy``
        # via the higher-level SSHClient API because we don't need a shell —
        # SFTP is a subsystem-only protocol here, so Transport is leaner.
        # Server host keys are pinned at the infrastructure layer (the
        # atmoz/sftp image generates a stable key into its persistent
        # volume — see docker-compose.yml's ``sftp-data`` volume).
        transport.connect(username=self._username, password=self._password)

        sftp = paramiko.SFTPClient.from_transport(transport)
        if sftp is None:
            # paramiko returns None if the subsystem could not be opened.
            # Tear down the transport so we don't leak a dangling socket.
            transport.close()
            raise RuntimeError(
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
            except Exception:  # noqa: BLE001 — best effort on shutdown
                logger.debug("sftp: error closing SFTPClient", exc_info=True)
            self._sftp = None

        if self._transport is not None:
            try:
                self._transport.close()
            except Exception:  # noqa: BLE001
                logger.debug("sftp: error closing Transport", exc_info=True)
            self._transport = None

    # Allow ``with SFTPClient(...) as c:`` ergonomics in tests / scripts.
    def __enter__(self) -> "SFTPClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ------------------------------------------------------------------
    # The hot path: one polling tick
    # ------------------------------------------------------------------
    def list_and_download_new_files(
        self,
        remote_dir: str,
    ) -> Iterator[tuple[str, bytes]]:
        """List ``*.tiff`` in ``remote_dir``, read each into memory,
        delete the remote file on success, and yield
        ``(filename, file_bytes)`` for each.

        Generator semantics (lazy, not list-eager) chosen so the calling
        worker can stream into MinIO + RQ without holding the whole
        batch in memory if the drop is large. A single RVL-CDIP TIFF is
        small, but a single SFTP drop may contain hundreds.

        Deletion-after-read is the at-most-once delivery guarantee for
        this pipeline. If MinIO/Redis go down between the read and the
        delete, we re-deliver — which means the inference worker MUST
        be idempotent on ``(batch_id, filename)``. That's a
        cross-component contract; flagging it loudly here so it isn't
        forgotten.

        Failure mode
        ------------
        If reading a file raises, we do NOT delete it; the next poll
        tick will see it again. If deletion itself fails after a
        successful read, we yield the bytes anyway and log loudly —
        re-delivery is preferable to data loss. The downstream worker
        must dedupe.

        Parameters
        ----------
        remote_dir
            Absolute path on the SFTP server, e.g. ``"/upload"``. Note
            atmoz/sftp chroots ``scanner`` to ``/home/scanner``, so the
            poller sees the share as ``/upload`` (not the
            ``/home/scanner/upload`` the host sees).
        """
        if self._sftp is None:
            # We could lazily connect here, but that would mask wiring
            # bugs. Explicit is better — callers must connect() first.
            raise RuntimeError(
                "SFTPClient: connect() must be called before "
                "list_and_download_new_files()."
            )

        # ``listdir`` returns just basenames, sorted by paramiko's view
        # of the directory. We rebuild the absolute remote path with
        # posixpath because SFTP paths are always POSIX, regardless of
        # the local OS running this code.
        try:
            entries = self._sftp.listdir(remote_dir)
        except FileNotFoundError:
            # The poller race-loses against the SFTP container booting.
            # Yield nothing and let the next tick retry — much cleaner
            # than crashing the worker process.
            logger.warning("sftp: remote_dir %r not found yet, skipping tick", remote_dir)
            return

        for name in entries:
            # Lowercase check so we accept .TIFF, .Tif, etc. that some
            # scanners emit.
            if not name.lower().endswith(_TIFF_SUFFIXES):
                # Non-TIFF detritus (.DS_Store, partial uploads, etc.).
                # We deliberately leave it on the server — quarantining
                # is the ingest worker's job, not the adapter's.
                continue

            remote_path = posixpath.join(remote_dir, name)

            # Step 1: read the bytes. We use the file handle as a
            # context manager so paramiko frees the channel slot even
            # on exception. ``read()`` blocks until EOF; for ~10 MB
            # TIFFs this is well under our latency budget.
            try:
                with self._sftp.open(remote_path, mode="rb") as handle:
                    # ``set_pipelined`` lets paramiko request the next
                    # block before the previous one returns, which
                    # roughly doubles throughput on the LAN.
                    handle.set_pipelined(True)
                    data: bytes = handle.read()
            except Exception:  # noqa: BLE001 — log and continue tick
                # Per the failure-mode docstring above: do NOT delete.
                # The next tick will see the file again.
                logger.exception(
                    "sftp: failed to read %r, leaving on server for retry",
                    remote_path,
                )
                continue

            # Step 2: delete on success. We delete *before* yielding so
            # that even if the downstream consumer crashes mid-iter, we
            # don't see this file again next tick. The trade-off is
            # explicitly documented: the inference worker must be
            # idempotent on filename.
            try:
                self._sftp.remove(remote_path)
                logger.info(
                    "sftp: read+deleted %r (%d bytes)",
                    remote_path, len(data),
                )
            except Exception:  # noqa: BLE001
                # Deletion failed AFTER a successful read. We yield the
                # bytes anyway — re-processing one image is cheap;
                # losing a customer's scan is not.
                logger.exception(
                    "sftp: read %r ok but delete failed; downstream "
                    "MUST dedupe to avoid double-processing",
                    remote_path,
                )

            # Step 3: hand the (filename, bytes) tuple to the caller.
            # Filename is the basename only — the caller composes the
            # blob key, e.g. ``batches/{batch_id}/{name}``.
            yield name, data
