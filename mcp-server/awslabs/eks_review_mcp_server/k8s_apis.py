# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
# Permission is hereby granted, free of charge, to any person obtaining a copy of
# this software and associated documentation files (the "Software"), to deal in
# the Software without restriction, including without limitation the rights to
# use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of
# the Software, and to permit persons to whom the Software is furnished to do so.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS
# FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR
# COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN
# AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION
# WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

"""Kubernetes API client for the EKS Review MCP Server."""

import atexit
import base64
import os
import stat
import tempfile
from awslabs.eks_review_mcp_server import __version__
from loguru import logger
from typing import Any, Optional


# ── CA certificate temp-file lifecycle ─────────────────────────────
# The kubernetes-client (v35) requires `ssl_ca_cert` to be a file path,
# so we have to materialize the CA bytes on disk for SSL verification.
# To avoid leaving cluster CAs scattered in /tmp on crash, we:
#   1. Write the file into a dedicated subdirectory we own.
#   2. Set permissions to 0600 so other local users can't read it.
#   3. Track every file we create in a process-global set.
#   4. Register an atexit hook that wipes all tracked files on normal exit
#      (covers KeyboardInterrupt, sys.exit, even when __del__ misses).
#   5. Sweep our subdirectory at import time to reap files from a prior
#      crashed run.
# The __del__ best-effort cleanup is preserved as a third safety net for
# long-running processes that recycle K8sApis instances.

_CA_TEMP_DIR = os.path.join(tempfile.gettempdir(), 'eks-review-ca')
_active_ca_paths: set[str] = set()
# Guards _active_ca_paths set mutations. CPython makes individual set ops
# atomic, but iteration during concurrent mutation is not safe. The MCP
# server is single-threaded today; this lock makes the contract explicit
# and future-proofs against threaded use.
import threading as _threading
_active_ca_paths_lock = _threading.Lock()


def _ensure_ca_temp_dir() -> str:
    """Create the dedicated CA temp directory with restrictive perms."""
    os.makedirs(_CA_TEMP_DIR, exist_ok=True)
    # 0700 — owner-only access. No-op on Windows.
    if os.name == 'posix':
        try:
            os.chmod(_CA_TEMP_DIR, 0o700)
        except OSError:
            pass
    return _CA_TEMP_DIR


def _cleanup_ca_file(path: str) -> None:
    """Best-effort removal of a single CA temp file."""
    try:
        if path and os.path.exists(path):
            os.unlink(path)
    except OSError:
        # Ignore — file may have been removed concurrently or perms changed.
        pass
    finally:
        with _active_ca_paths_lock:
            _active_ca_paths.discard(path)


def _cleanup_all_ca_files() -> None:
    """Remove every CA temp file this process tracked. Registered with atexit."""
    # Snapshot the set under the lock so a concurrent mutation can't race us.
    with _active_ca_paths_lock:
        snapshot = list(_active_ca_paths)
    for path in snapshot:
        _cleanup_ca_file(path)


def _sweep_stale_ca_files() -> None:
    """Remove CA temp files left by a previously-crashed process.

    Only sweeps our dedicated subdirectory to avoid touching unrelated
    files. This runs once at import time.
    """
    if not os.path.isdir(_CA_TEMP_DIR):
        return
    try:
        for name in os.listdir(_CA_TEMP_DIR):
            full = os.path.join(_CA_TEMP_DIR, name)
            try:
                if os.path.isfile(full):
                    os.unlink(full)
            except OSError:
                pass
    except OSError:
        pass


# Register process-wide cleanup once. atexit fires on normal exit, sys.exit,
# and unhandled exceptions — but not on os._exit, signals without handlers,
# or hard kills. The startup sweep covers those cases on the next run.
atexit.register(_cleanup_all_ca_files)
_sweep_stale_ca_files()


class K8sApis:
    """Class for managing Kubernetes API client.

    This class provides a simplified interface for interacting with the Kubernetes API
    using the official Kubernetes Python client.
    """

    def __init__(self, endpoint, token, ca_data):
        """Initialize Kubernetes API client.

        Args:
            endpoint: Kubernetes API endpoint
            token: Authentication token
            ca_data: CA certificate data (base64 encoded) - required for SSL verification
        """
        try:
            from kubernetes import client, dynamic

            configuration = client.Configuration()
            configuration.host = endpoint
            configuration.api_key = {'authorization': f'Bearer {token}'}

            # Track the CA cert file path for cleanup paths (atexit, __del__).
            self._ca_cert_file_path: Optional[str] = None

            # Always enable SSL verification with CA data
            configuration.verify_ssl = True

            # Materialize the CA bytes into our dedicated temp dir.
            try:
                ca_dir = _ensure_ca_temp_dir()
                # delete=False because we must hand the path to the
                # kubernetes-client; we own cleanup via atexit + __del__.
                with tempfile.NamedTemporaryFile(
                    mode='wb',
                    suffix='.pem',
                    prefix='ca-',
                    dir=ca_dir,
                    delete=False,
                ) as ca_cert_file:
                    ca_cert_data = base64.b64decode(ca_data)
                    ca_cert_file.write(ca_cert_data)
                    self._ca_cert_file_path = ca_cert_file.name

                # 0600 — owner-only read/write. Defense-in-depth in case
                # a file slips past cleanup.
                if os.name == 'posix':
                    try:
                        os.chmod(self._ca_cert_file_path, stat.S_IRUSR | stat.S_IWUSR)
                    except OSError as e:
                        logger.warning(
                            f'Could not chmod CA cert file {self._ca_cert_file_path}: {e}'
                        )

                # Register with the process-global cleanup registry.
                with _active_ca_paths_lock:
                    _active_ca_paths.add(self._ca_cert_file_path)

                # setattr avoids static-type complaints on dynamic config.
                setattr(configuration, 'ssl_ca_cert', self._ca_cert_file_path)
            except Exception:
                # Roll back the partially-created file before propagating.
                if self._ca_cert_file_path:
                    _cleanup_ca_file(self._ca_cert_file_path)
                    self._ca_cert_file_path = None
                raise

            # Configure HTTP proxy settings if environment variables are present
            self._configure_proxy_settings(configuration)

            # If anything fails after the CA file was registered, undo the
            # registration so it isn't held until atexit. Without this guard
            # a bad endpoint or unreachable cluster would orphan the temp
            # file in long-running processes.
            try:
                # Create base API client
                self.api_client = client.ApiClient(configuration)

                # Set user-agent directly on the ApiClient
                self.api_client.user_agent = f'awslabs/mcp/eks-review-mcp-server/{__version__}'

                # Create dynamic client
                self.dynamic_client = dynamic.DynamicClient(self.api_client)
            except Exception:
                if self._ca_cert_file_path:
                    _cleanup_ca_file(self._ca_cert_file_path)
                    self._ca_cert_file_path = None
                raise

        except ImportError:
            logger.error('kubernetes package not installed')
            raise

    def _configure_proxy_settings(self, config):
        """Configure proxy settings for Kubernetes client from environment variables."""
        # Get proxy URL (HTTPS proxy takes precedence over HTTP proxy)
        proxy_url = (
            os.environ.get('HTTPS_PROXY')
            or os.environ.get('https_proxy')
            or os.environ.get('HTTP_PROXY')
            or os.environ.get('http_proxy')
        )

        if not proxy_url:
            return

        logger.debug(f'Configuring proxy: {proxy_url}')
        config.proxy = proxy_url

    def list_resources(
        self,
        kind: str,
        api_version: str,
        namespace: Optional[str] = None,
        label_selector: Optional[str] = None,
        field_selector: Optional[str] = None,
        **kwargs,
    ) -> Any:
        """List Kubernetes resources of a specific kind using dynamic client.

        Args:
            kind: Resource kind (e.g., 'Pod', 'Service')
            api_version: API version (e.g., 'v1', 'apps/v1')
            namespace: Namespace to list resources from (optional)
            label_selector: Label selector to filter resources (optional)
            field_selector: Field selector to filter resources (optional)
            **kwargs: Additional arguments for the API call

        Returns:
            The API response containing the list of resources
        """
        try:
            # Get the API resource
            resource = self.dynamic_client.resources.get(api_version=api_version, kind=kind)

            # Prepare kwargs for the list operation
            list_kwargs = {}
            if label_selector:
                list_kwargs['label_selector'] = label_selector
            if field_selector:
                list_kwargs['field_selector'] = field_selector

            # Add any additional kwargs
            list_kwargs.update(kwargs)

            # List resources
            if namespace:
                return resource.get(namespace=namespace, **list_kwargs)
            else:
                return resource.get(**list_kwargs)

        except Exception as e:
            # Re-raise with more context
            raise ValueError(f'Error listing {kind} resources: {str(e)}')

    def close(self) -> None:
        """Explicitly release the temp CA file. Safe to call multiple times.

        Long-running processes that recycle K8sApis instances should call
        this when done to avoid waiting for GC. Idempotent.
        """
        if self._ca_cert_file_path:
            _cleanup_ca_file(self._ca_cert_file_path)
            self._ca_cert_file_path = None

    def __del__(self):
        """Best-effort cleanup when the object is garbage collected.

        Third safety net behind close() and the atexit hook. Intentionally
        silent — interpreter teardown can race with module unloads, so any
        exception here is suppressed.
        """
        try:
            self.close()
        except Exception:
            pass
