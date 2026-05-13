from __future__ import annotations

import hvac


class VaultClient:
    """Minimal Vault KV v2 client. Constructed once in lifespan; stored on app.state.vault."""

    def __init__(self, addr: str, token: str) -> None:
        self._client = hvac.Client(url=addr, token=token)

    def get_secret(self, path: str) -> dict:
        """
        Args: Mount-relative path, 'jwt' for the secret at 'secret/data/jwt'
        Returns: The 'data' dict from the KV v2 response
        """
        try:
            response = self._client.secrets.kv.v2.read_secret_version(
                path=path,
                raise_on_deleted_version=True,
            )
            return response["data"]["data"]
        except Exception as exc:
            raise RuntimeError(
                f"Vault secret read failed for path '{path}': {exc}"
            ) from exc
