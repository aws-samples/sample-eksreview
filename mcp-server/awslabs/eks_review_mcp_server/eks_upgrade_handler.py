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

"""Handler for EKS upgrade readiness checks in the EKS MCP Server.

Performs comprehensive pre-upgrade analysis covering:
- AWS EKS Upgrade Insights (deprecated APIs, addon compat, health)
- Addon version compatibility with target K8s version
- Data plane readiness (node versions, AMI types, subnet IPs)
- Workload readiness (PDBs, replicas, probes, topology)
- Third-party component inventory (CA, Karpenter, cert-manager, etc.)
- API deprecation scanning (live resources + Helm release manifests)
"""

import base64
import gzip
import json
import re
from pathlib import Path

import yaml
from awslabs.eks_review_mcp_server import __version__
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_upgrade_response, _group_resources_by_namespace
from awslabs.eks_review_mcp_server.models import UpgradeCheckResponse
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, List, Optional, Tuple


def _parse_k8s_version(version_str: str) -> Tuple[int, int]:
    """Parse a Kubernetes version string like '1.32' or 'v1.32.0' into (major, minor)."""
    m = re.search(r'(\d+)\.(\d+)', version_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 0, 0


def _next_minor_version(current: str) -> str:
    """Compute the next minor version string from current (e.g., '1.32' -> '1.33')."""
    major, minor = _parse_k8s_version(current)
    return f'{major}.{minor + 1}'


def _version_lte(v1: str, v2: str) -> bool:
    """Check if version v1 <= v2 (e.g., 'v1.25.0' <= 'v1.33.0')."""
    m1, n1 = _parse_k8s_version(v1)
    m2, n2 = _parse_k8s_version(v2)
    return (m1, n1) <= (m2, n2)


class EKSUpgradeHandler:
    """Handler for EKS upgrade readiness checks."""

    def __init__(self, mcp, client_cache):
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()
        self.deprecation_db = self._load_deprecation_db()

        # Check boto3 version for describe_cluster_versions support
        try:
            import boto3
            from packaging.version import Version
            boto3_ver = boto3.__version__
            # describe_cluster_versions was added around boto3 1.35.81 (Dec 2024)
            if Version(boto3_ver) < Version('1.35.81'):
                logger.warning(
                    f'boto3 {boto3_ver} may not support describe_cluster_versions API. '
                    'Upgrade to boto3>=1.35.81 for full upgrade readiness features '
                    '(version availability check, support status). '
                    'The tool will still work with degraded functionality.'
                )
        except Exception:
            pass

        # Register the tool
        self.mcp.tool(name='check_eks_upgrade_readiness')(self.check_eks_upgrade_readiness)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_upgrade_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'Failed to load upgrade check registry: {e}')
            return {}

    def _load_deprecation_db(self) -> List[Dict[str, str]]:
        """Load the K8s API deprecation database.

        Tries to fetch the latest from Pluto's GitHub repo first.
        Falls back to the bundled YAML if the fetch fails (offline, rate-limited, etc.).
        This ensures the database stays current even if the MCP server isn't upgraded.

        The fetched YAML is treated as untrusted data: it must pass structural
        and sanity checks before we use it. If validation fails — corrupted file,
        wrong schema, suspiciously fewer entries than the bundled baseline, or
        oversize response — we fall back to the bundled file. This protects
        against accidental corruption or upstream tampering on `master` without
        sacrificing the live-update benefit.
        """
        bundled_path = Path(__file__).parent / 'data' / 'k8s_deprecated_versions.yaml'
        url = 'https://raw.githubusercontent.com/FairwindsOps/pluto/master/versions.yaml'

        # Load the bundled file first — used both as the fallback AND as a
        # baseline for sanity-checking the fetched data.
        bundled_entries: List[Dict[str, str]] = []
        try:
            with open(bundled_path, 'r') as f:
                bundled_data = yaml.safe_load(f)
            bundled_entries = bundled_data.get('deprecated-versions', []) or []
        except Exception as e:
            logger.warning(f'Could not pre-load bundled deprecation DB: {e}')

        # Try fetching latest from GitHub
        fetched = self._fetch_remote_deprecation_db(url, bundled_entries)
        if fetched is not None:
            logger.info(
                f'Loaded {len(fetched)} API deprecation entries from GitHub (latest)'
            )
            return fetched

        # Fall back to bundled file
        if bundled_entries:
            logger.info(
                f'Loaded {len(bundled_entries)} API deprecation entries from bundled file'
            )
            return bundled_entries

        logger.error('Failed to load deprecation database from GitHub or bundled file')
        return []

    # Validation knobs for the fetched deprecation DB.
    _DEPRECATION_DB_MAX_BYTES = 5 * 1024 * 1024  # 5 MB hard cap on response body
    _DEPRECATION_DB_MIN_ENTRIES = 50             # baseline floor — never accept a near-empty file
    # Each entry must be a dict with `version` and `kind` keys present, and
    # at least one of `removed-in` or `deprecated-in` populated. Upstream
    # data legitimately has rows where one of those fields is empty (e.g.
    # APIs deprecated but not yet removed), so we tolerate empty strings
    # on individual lifecycle fields as long as the entry has identity
    # (version + kind) and lifecycle context (one of the timing fields).
    _DEPRECATION_DB_IDENTITY_KEYS = ('version', 'kind')
    _DEPRECATION_DB_LIFECYCLE_KEYS = ('removed-in', 'deprecated-in')
    # Forward-compat headroom: allow up to 5% of rows to be malformed
    # before rejecting the whole file.
    _DEPRECATION_DB_MALFORMED_TOLERANCE = 0.05

    def _fetch_remote_deprecation_db(
        self,
        url: str,
        bundled_entries: List[Dict[str, str]],
    ) -> Optional[List[Dict[str, str]]]:
        """Fetch and validate the upstream deprecation YAML.

        Returns the parsed entries on success, or None if any check fails so
        the caller can fall back to the bundled file. Treats the response as
        untrusted: enforces size, schema, and shrink-detection checks before
        accepting it.
        """
        try:
            import urllib.request

            req = urllib.request.Request(
                url,
                headers={
                    'User-Agent': f'awslabs/mcp/eks-review-mcp-server/{__version__}',
                    'Accept': 'text/plain, application/x-yaml, application/yaml',
                },
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                # Reject obviously-wrong content types (e.g. an HTML error page).
                ctype = (resp.headers.get('Content-Type') or '').lower()
                if ctype and not any(
                    t in ctype for t in ('text/plain', 'application/yaml',
                                          'application/x-yaml', 'text/yaml',
                                          'application/octet-stream')
                ):
                    logger.warning(
                        f'Rejecting deprecation DB fetch: unexpected Content-Type {ctype!r}'
                    )
                    return None

                # Read with a hard byte cap so a hostile redirect or oversized
                # response cannot exhaust memory.
                body = resp.read(self._DEPRECATION_DB_MAX_BYTES + 1)
                if len(body) > self._DEPRECATION_DB_MAX_BYTES:
                    logger.warning(
                        f'Rejecting deprecation DB fetch: body exceeds '
                        f'{self._DEPRECATION_DB_MAX_BYTES} bytes'
                    )
                    return None

            data = yaml.safe_load(body.decode('utf-8'))
        except Exception as e:
            logger.debug(f'Could not fetch latest deprecation DB from GitHub: {e}')
            return None

        return self._validate_deprecation_db(data, bundled_entries)

    def _validate_deprecation_db(
        self,
        data: Any,
        bundled_entries: List[Dict[str, str]],
    ) -> Optional[List[Dict[str, str]]]:
        """Structural + sanity validation of fetched YAML. Returns entries or None."""
        if not isinstance(data, dict):
            logger.warning('Rejecting deprecation DB: top-level is not a mapping')
            return None

        entries = data.get('deprecated-versions')
        if not isinstance(entries, list):
            logger.warning('Rejecting deprecation DB: deprecated-versions missing or not a list')
            return None

        # Shrink detection — a real upstream update should keep or grow the
        # entry count. A sudden drop suggests corruption or tampering.
        if bundled_entries and len(entries) < max(
            self._DEPRECATION_DB_MIN_ENTRIES,
            len(bundled_entries) // 2,
        ):
            logger.warning(
                f'Rejecting deprecation DB: only {len(entries)} entries '
                f'(bundled has {len(bundled_entries)})'
            )
            return None

        if len(entries) < self._DEPRECATION_DB_MIN_ENTRIES:
            logger.warning(
                f'Rejecting deprecation DB: only {len(entries)} entries '
                f'(minimum {self._DEPRECATION_DB_MIN_ENTRIES})'
            )
            return None

        # Per-entry schema check. Allow up to MALFORMED_TOLERANCE of rows
        # to be malformed for forward-compat with new optional fields and
        # quirks in upstream data. Each entry must be a dict with both
        # identity keys present, and at least one of the lifecycle keys
        # populated.
        bad = 0
        bad_examples = []
        for entry in entries:
            if not isinstance(entry, dict):
                bad += 1
                if len(bad_examples) < 3:
                    bad_examples.append(repr(entry)[:80])
                continue
            has_identity = all(
                isinstance(entry.get(k), str) and entry.get(k)
                for k in self._DEPRECATION_DB_IDENTITY_KEYS
            )
            has_lifecycle = any(
                isinstance(entry.get(k), str) and entry.get(k)
                for k in self._DEPRECATION_DB_LIFECYCLE_KEYS
            )
            if not (has_identity and has_lifecycle):
                bad += 1
                if len(bad_examples) < 3:
                    bad_examples.append(repr(entry)[:80])

        max_allowed_bad = max(1, int(len(entries) * self._DEPRECATION_DB_MALFORMED_TOLERANCE))
        if bad > max_allowed_bad:
            logger.warning(
                f'Rejecting deprecation DB: {bad} of {len(entries)} entries '
                f'are malformed (tolerance {max_allowed_bad}). '
                f'Examples: {bad_examples}'
            )
            return None

        return entries

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        all_checks = {}
        for category in self.check_registry.values():
            if isinstance(category, dict):
                all_checks.update(category)
        return all_checks

    def _get_check_info(self, check_id: str) -> Dict[str, Any]:
        return self._get_all_checks().get(check_id, {})

    def _create_check_result(self, check_id: str, compliant: bool,
                             impacted_resources: List[str], details: str,
                             upgrade_timing: str = '') -> Dict[str, Any]:
        check_info = self._get_check_info(check_id)
        result = {
            'check_id': check_id,
            'check_name': check_info.get('name', f'Check {check_id}'),
            'compliant': compliant,
            'severity': check_info.get('severity', 'Medium'),
            'impacted_resources': impacted_resources,
            'details': details,
        }
        if upgrade_timing:
            result['upgrade_timing'] = upgrade_timing
        return result

    def _create_check_error_result(self, check_id: str, error_msg: str) -> Dict[str, Any]:
        check_info = self._get_check_info(check_id)
        return {
            'check_id': check_id,
            'check_name': check_info.get('name', f'Check {check_id}'),
            'compliant': False,
            'severity': check_info.get('severity', 'Medium'),
            'impacted_resources': [],
            'details': f'Check failed with error: {error_msg}',
        }

    def _create_error_response(self, cluster_name: str, error_msg: str) -> UpgradeCheckResponse:
        return UpgradeCheckResponse(
            isError=True,
            content=[TextContent(type='text', text=f'Failed to check upgrade readiness for {cluster_name}: {error_msg}')],
            check_results=[],
            overall_ready=False,
            blockers=0,
            warnings=0,
            current_version='unknown',
            target_version='unknown',
            summary=f'Failed: {error_msg}',
        )

    # ── Main tool entry point ───────────────────

    async def check_eks_upgrade_readiness(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for upgrade readiness.'
        ),
        target_version: Optional[str] = Field(
            None, description='Target Kubernetes version (e.g., "1.33"). If omitted, auto-detects next minor version.'
        ),
        namespace: Optional[str] = Field(
            None, description='Optional namespace to limit workload checks scope.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
    ) -> UpgradeCheckResponse:
        """Check EKS cluster upgrade readiness with comprehensive pre-flight analysis.

        This tool performs 38 checks across 10 categories to assess whether a cluster
        is ready to upgrade to the target Kubernetes version. It covers everything
        AWS Upgrade Insights checks plus additional areas Insights misses:

        - Control plane version and support status
        - AWS EKS Upgrade Insights (deprecated APIs from audit logs)
        - EKS managed addon version compatibility
        - Self-managed addon detection
        - Data plane readiness (node versions, AMI types, subnet IPs)
        - Workload readiness (PDBs, replicas, probes, topology)
        - Third-party component inventory (CA, Karpenter, cert-manager, etc.)
        - API deprecation scanning in live resources AND Helm release manifests

        Returns a go/no-go verdict with blocker count, warning count, and
        detailed findings for each check.
        """
        try:
            logger.info(f'Starting upgrade readiness check for cluster: {cluster_name}')

            # Get K8s client
            try:
                k8s_client = self.client_cache.get_client(cluster_name, region=region)
            except Exception as e:
                logger.error(f'Failed to get K8s client for {cluster_name}: {e}')
                return self._create_error_response(cluster_name, str(e))

            # Initialize shared data
            shared_data = await self._initialize_shared_data(
                k8s_client, cluster_name, target_version, namespace, region
            )
            if not shared_data:
                return self._create_error_response(cluster_name, 'Failed to initialize shared data')

            current_ver = shared_data['current_version']
            target_ver = shared_data['target_version']

            # Early exit: cluster already at or beyond target version
            c_maj, c_min = _parse_k8s_version(current_ver)
            t_maj, t_min = _parse_k8s_version(target_ver)
            if (t_maj, t_min) <= (c_maj, c_min):
                return UpgradeCheckResponse(
                    isError=False,
                    content=[TextContent(type='text',
                        text=f'Cluster {cluster_name} is already on version {current_ver}. '
                             f'Target {target_ver} is not newer. No upgrade needed.')],
                    check_results=[],
                    overall_ready=True,
                    blockers=0,
                    warnings=0,
                    current_version=current_ver,
                    target_version=target_ver,
                    summary=f'Cluster {cluster_name} is on {current_ver} — already at or beyond {target_ver}. No upgrade needed.',
                )

            # Early exit: check if target version exists in EKS
            try:
                versions_resp = shared_data['eks_client'].describe_cluster_versions(
                    clusterVersions=[target_ver]
                )
                available = versions_resp.get('clusterVersions', [])
                if not available:
                    # Target version not available — check what the latest is
                    all_versions_resp = shared_data['eks_client'].describe_cluster_versions(
                        versionStatus='STANDARD_SUPPORT'
                    )
                    all_versions = all_versions_resp.get('clusterVersions', [])
                    latest = max(
                        (v.get('clusterVersion', '') for v in all_versions),
                        key=lambda v: _parse_k8s_version(v),
                        default='unknown'
                    )
                    return UpgradeCheckResponse(
                        isError=False,
                        content=[TextContent(type='text',
                            text=f'Target version {target_ver} is not available in Amazon EKS. '
                                 f'Cluster {cluster_name} is on {current_ver}. '
                                 f'Latest EKS version in standard support: {latest}.')],
                        check_results=[],
                        overall_ready=True,
                        blockers=0,
                        warnings=0,
                        current_version=current_ver,
                        target_version=target_ver,
                        summary=f'Target {target_ver} not available in EKS. '
                                f'Cluster is on {current_ver}. Latest: {latest}.',
                    )

                # Store version metadata for U1 check
                ver_info = available[0]
                shared_data['target_version_info'] = ver_info
                shared_data['target_version_status'] = ver_info.get('versionStatus', 'unknown')
                logger.info(f'Target version {target_ver} status: {shared_data["target_version_status"]}')

            except Exception as e:
                # API might not be available in older boto3 — continue with checks
                logger.debug(f'describe_cluster_versions not available: {e}')
                shared_data['target_version_info'] = {}
                shared_data['target_version_status'] = 'unknown'

            # Run all checks
            check_results = []
            all_checks = self._get_all_checks()

            for check_id in sorted(all_checks.keys()):
                check_info = all_checks[check_id]
                if not check_info.get('enabled', True):
                    continue
                try:
                    logger.info(f'Running upgrade check {check_id}')
                    result = await self._execute_check(check_id, shared_data)
                    check_results.append(result)
                except Exception as e:
                    logger.error(f'Error in upgrade check {check_id}: {e}')
                    check_results.append(self._create_check_error_result(check_id, str(e)))

            # Compute verdict
            # Only Critical severity + timing=before = true blocker (will break the upgrade)
            # Critical + timing=after = urgent post-upgrade action, not a blocker
            # High/Medium/Low severity = warning
            blockers = sum(
                1 for r in check_results
                if not r['compliant']
                and r.get('severity', '').lower() == 'critical'
                and r.get('upgrade_timing', 'before') == 'before'
            )
            warnings = sum(
                1 for r in check_results
                if not r['compliant'] and not (
                    r.get('severity', '').lower() == 'critical'
                    and r.get('upgrade_timing', 'before') == 'before'
                )
            )
            passed = sum(1 for r in check_results if r['compliant'])
            overall_ready = blockers == 0

            verdict = 'READY' if overall_ready else f'NOT READY ({blockers} blockers)'
            summary = (
                f'Upgrade readiness for {cluster_name} ({current_ver} -> {target_ver}): '
                f'{verdict}. {passed} passed, {blockers} blockers, {warnings} warnings.'
            )

            # Compact the check results for LLM consumption (same pattern as review handlers)
            content_text = json.dumps(
                compact_upgrade_response(
                    summary, check_results,
                    current_ver, target_ver,
                    blockers, warnings,
                ),
                separators=(',', ':'),
            )

            return UpgradeCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_ready=overall_ready,
                blockers=blockers,
                warnings=warnings,
                current_version=current_ver,
                target_version=target_ver,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in upgrade readiness check: {e}')
            return self._create_error_response(cluster_name, str(e))

    # ── Shared data initialization ──────────────

    async def _initialize_shared_data(self, k8s_client, cluster_name: str,
                                       target_version: Optional[str],
                                       namespace: Optional[str],
                                       region: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch all data once to share across checks."""
        try:
            sd = {}
            sd['k8s_client'] = k8s_client
            sd['cluster_name'] = cluster_name
            sd['namespace'] = namespace

            # AWS clients
            eks_client = AwsHelper.create_boto3_client('eks', region)
            ec2_client = AwsHelper.create_boto3_client('ec2', region)
            sd['eks_client'] = eks_client
            sd['ec2_client'] = ec2_client

            # Cluster info
            try:
                resp = eks_client.describe_cluster(name=cluster_name)
                sd['cluster_info'] = resp['cluster']
                sd['current_version'] = sd['cluster_info']['version']
                logger.info(f'Cluster {cluster_name} is on K8s {sd["current_version"]}')
            except Exception as e:
                logger.error(f'Failed to describe cluster: {e}')
                return None

            # Target version
            if target_version:
                sd['target_version'] = target_version.lstrip('v')
            else:
                sd['target_version'] = _next_minor_version(sd['current_version'])
            logger.info(f'Target version: {sd["target_version"]}')

            # Upgrade Insights
            try:
                insights_resp = eks_client.list_insights(
                    clusterName=cluster_name,
                    filter={'categories': ['UPGRADE_READINESS']}
                )
                sd['insights'] = insights_resp.get('insights', [])
                # Fetch details for non-passing insights
                sd['insight_details'] = {}
                for ins in sd['insights']:
                    if ins.get('insightStatus', {}).get('status') != 'PASSING':
                        try:
                            detail = eks_client.describe_insight(
                                clusterName=cluster_name, id=ins['id']
                            )
                            sd['insight_details'][ins['id']] = detail.get('insight', {})
                        except Exception:
                            pass
                logger.info(f'Fetched {len(sd["insights"])} upgrade insights')
            except Exception as e:
                logger.warning(f'Failed to fetch upgrade insights: {e}')
                import traceback
                logger.warning(f'Insights traceback: {traceback.format_exc()}')
                sd['insights'] = []
                sd['insight_details'] = {}

            # Addons
            try:
                addon_names = eks_client.list_addons(clusterName=cluster_name).get('addons', [])
                sd['addon_names'] = addon_names
                sd['addon_details'] = {}
                for name in addon_names:
                    try:
                        sd['addon_details'][name] = eks_client.describe_addon(
                            clusterName=cluster_name, addonName=name
                        ).get('addon', {})
                    except Exception:
                        pass
                logger.info(f'Fetched {len(addon_names)} addons')
            except Exception as e:
                logger.warning(f'Failed to fetch addons: {e}')
                sd['addon_names'] = []
                sd['addon_details'] = {}

            # Addon versions for target
            sd['addon_target_versions'] = {}
            for name in sd.get('addon_names', []):
                try:
                    resp = eks_client.describe_addon_versions(
                        addonName=name,
                        kubernetesVersion=sd['target_version']
                    )
                    sd['addon_target_versions'][name] = resp.get('addons', [{}])[0]
                except Exception:
                    pass

            # Node groups
            try:
                ng_names = eks_client.list_nodegroups(clusterName=cluster_name).get('nodegroups', [])
                sd['nodegroup_names'] = ng_names
                sd['nodegroup_details'] = {}
                for ng in ng_names:
                    try:
                        sd['nodegroup_details'][ng] = eks_client.describe_nodegroup(
                            clusterName=cluster_name, nodegroupName=ng
                        ).get('nodegroup', {})
                    except Exception:
                        pass
                logger.info(f'Fetched {len(ng_names)} node groups')
            except Exception as e:
                logger.warning(f'Failed to fetch node groups: {e}')
                sd['nodegroup_names'] = []
                sd['nodegroup_details'] = {}

            # Subnets
            try:
                subnet_ids = sd['cluster_info'].get('resourcesVpcConfig', {}).get('subnetIds', [])
                if subnet_ids:
                    sd['subnets'] = ec2_client.describe_subnets(SubnetIds=subnet_ids).get('Subnets', [])
                else:
                    sd['subnets'] = []
            except Exception as e:
                logger.warning(f'Failed to fetch subnets: {e}')
                sd['subnets'] = []

            # K8s resources
            kwargs = {'namespace': namespace} if namespace else {}

            try:
                nodes = k8s_client.list_resources(kind='Node', api_version='v1')
                sd['nodes'] = nodes.items if hasattr(nodes, 'items') else []
            except Exception:
                sd['nodes'] = []

            try:
                deps = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', **kwargs)
                sd['deployments'] = deps.items if hasattr(deps, 'items') else []
            except Exception:
                sd['deployments'] = []

            try:
                sts = k8s_client.list_resources(kind='StatefulSet', api_version='apps/v1', **kwargs)
                sd['statefulsets'] = sts.items if hasattr(sts, 'items') else []
            except Exception:
                sd['statefulsets'] = []

            try:
                pdbs = k8s_client.list_resources(kind='PodDisruptionBudget', api_version='policy/v1', **kwargs)
                sd['pdbs'] = pdbs.items if hasattr(pdbs, 'items') else []
            except Exception:
                sd['pdbs'] = []

            try:
                pods = k8s_client.list_resources(kind='Pod', api_version='v1', **kwargs)
                sd['pods'] = pods.items if hasattr(pods, 'items') else []
            except Exception:
                sd['pods'] = []

            # kube-system deployments (for third-party detection)
            try:
                ks_deps = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', namespace='kube-system')
                sd['kube_system_deployments'] = ks_deps.items if hasattr(ks_deps, 'items') else []
            except Exception:
                sd['kube_system_deployments'] = []

            try:
                ks_ds = k8s_client.list_resources(kind='DaemonSet', api_version='apps/v1', namespace='kube-system')
                sd['kube_system_daemonsets'] = ks_ds.items if hasattr(ks_ds, 'items') else []
            except Exception:
                sd['kube_system_daemonsets'] = []

            # Helm release secrets (for API deprecation scan)
            try:
                helm_secrets = k8s_client.list_resources(
                    kind='Secret', api_version='v1',
                    label_selector='owner=helm'
                )
                sd['helm_secrets'] = helm_secrets.items if hasattr(helm_secrets, 'items') else []
                logger.info(f'Fetched {len(sd["helm_secrets"])} Helm release secrets')
            except Exception:
                sd['helm_secrets'] = []

            # kube-system ConfigMaps (for kube-proxy IPVS check)
            try:
                ks_cms = k8s_client.list_resources(kind='ConfigMap', api_version='v1', namespace='kube-system')
                sd['kube_system_configmaps'] = ks_cms.items if hasattr(ks_cms, 'items') else []
            except Exception:
                sd['kube_system_configmaps'] = []

            # Karpenter namespace deployments
            try:
                karp_deps = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', namespace='karpenter')
                sd['karpenter_deployments'] = karp_deps.items if hasattr(karp_deps, 'items') else []
            except Exception:
                sd['karpenter_deployments'] = []

            # Third-party namespace deployments (fetched once, shared by U20, U34)
            sd['third_party_workloads'] = []
            for ns in ['cert-manager', 'istio-system', 'argocd', 'monitoring', 'flux-system', 'ingress-nginx', 'velero']:
                try:
                    ns_deps = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', namespace=ns)
                    sd['third_party_workloads'].extend(ns_deps.items if hasattr(ns_deps, 'items') else [])
                except Exception:
                    pass
            # Also DaemonSets from ingress-nginx
            try:
                nginx_ds = k8s_client.list_resources(kind='DaemonSet', api_version='apps/v1', namespace='ingress-nginx')
                sd['third_party_workloads'].extend(nginx_ds.items if hasattr(nginx_ds, 'items') else [])
            except Exception:
                pass

            # CRDs (for third-party API deprecation check)
            try:
                crds = k8s_client.list_resources(kind='CustomResourceDefinition', api_version='apiextensions.k8s.io/v1')
                sd['crds'] = crds.items if hasattr(crds, 'items') else []
            except Exception:
                sd['crds'] = []

            # MISCONFIGURATION insights (separate from UPGRADE_READINESS)
            try:
                misconfig_resp = eks_client.list_insights(
                    clusterName=cluster_name,
                    filter={'categories': ['MISCONFIGURATION']}
                )
                sd['misconfig_insights'] = misconfig_resp.get('insights', [])
            except Exception:
                sd['misconfig_insights'] = []

            return sd

        except Exception as e:
            logger.error(f'Failed to initialize shared data: {e}')
            return None

    # ── Check dispatcher ────────────────────────

    async def _execute_check(self, check_id: str, sd: Dict[str, Any]) -> Dict[str, Any]:
        methods = {
            'U1': self._check_version_status,
            'U2': self._check_target_available,
            'U3': self._check_multi_hop,
            'U4': self._check_upgrade_insights,
            'U5': self._check_addon_compat,
            'U6': self._check_self_managed_addons,
            'U7': self._check_addon_health,
            'U8': self._check_kubelet_skew,
            'U9': self._check_ami_type,
            'U10': self._check_nodegroup_pending,
            'U11': self._check_subnet_ips,
            'U12': self._check_pdb_coverage,
            'U13': self._check_single_replica,
            'U14': self._check_readiness_probes,
            'U15': self._check_topology,
            'U16': self._check_min_ready_seconds,
            'U17': self._check_termination_grace,
            'U18': self._check_cluster_autoscaler,
            'U19': self._check_karpenter,
            'U20': self._check_third_party_inventory,
            'U21': self._check_control_plane_logging,
            'U22': self._check_cluster_health,
            'U23': self._check_kube_proxy_skew,
            'U24': self._check_deprecated_apis_live,
            'U25': self._check_deprecated_apis_helm,
            'U26': self._check_deprecated_apis_warning,
            'U27': self._check_node_subnet_ips,
            'U28': self._check_pod_subnet_ips,
            'U29': self._check_ec2_limits,
            'U30': self._check_ebs_gp2_limits,
            'U31': self._check_ebs_gp3_limits,
            'U32': self._check_self_managed_ng_updates,
            'U33': self._check_kube_proxy_ipvs,
            'U34': self._check_ingress_nginx_retirement,
            'U35': self._check_docker_socket,
            'U36': self._check_insufficient_replicas,
            'U37': self._check_misconfig_insights,
            'U38': self._check_third_party_api_deprecations,
        }
        method = methods.get(check_id)
        if method:
            return await method(sd)
        return self._create_check_error_result(check_id, f'Not implemented: {check_id}')

    # ── Control Plane Checks (U1-U3) ────────────

    async def _check_version_status(self, sd: Dict) -> Dict:
        """U1: Current version and support status."""
        ver = sd['current_version']
        cluster = sd['cluster_info']
        platform_ver = cluster.get('platformVersion', 'unknown')

        # Use describe_cluster_versions data if available
        target_info = sd.get('target_version_info', {})
        target_status = sd.get('target_version_status', 'unknown')

        # Also check current version support status
        try:
            current_info_resp = sd['eks_client'].describe_cluster_versions(
                clusterVersions=[ver]
            )
            current_versions = current_info_resp.get('clusterVersions', [])
            if current_versions:
                cv = current_versions[0]
                current_status = cv.get('versionStatus', 'unknown')
                eos_standard = cv.get('endOfStandardSupportDate', '')
                eos_extended = cv.get('endOfExtendedSupportDate', '')
                details = (
                    f'Current version: {ver} ({current_status}), '
                    f'Platform: {platform_ver}. '
                    f'Target: {sd["target_version"]} ({target_status}).'
                )
                if eos_standard:
                    details += f' Standard support ends: {str(eos_standard)[:10]}.'
                if current_status == 'EXTENDED_SUPPORT':
                    details += ' WARNING: Current version is in extended support (higher cost).'
                    return self._create_check_result('U1', False, [f'{ver} ({current_status})'], details)
                return self._create_check_result('U1', True, [], details)
        except Exception:
            pass

        details = (
            f'Current Kubernetes version: {ver}, '
            f'Platform version: {platform_ver}.'
        )
        return self._create_check_result('U1', True, [], details)

    async def _check_target_available(self, sd: Dict) -> Dict:
        """U2: Target version is the next sequential minor."""
        current = sd['current_version']
        target = sd['target_version']
        expected_next = _next_minor_version(current)
        if target == expected_next:
            return self._create_check_result('U2', True, [],
                f'Target version {target} is the next minor version after {current}.')
        # Could be a multi-hop or invalid
        c_maj, c_min = _parse_k8s_version(current)
        t_maj, t_min = _parse_k8s_version(target)
        if t_min <= c_min:
            return self._create_check_result('U2', False,
                [f'Target {target} is not newer than current {current}'],
                f'Target version {target} must be newer than current {current}. EKS does not support downgrades.',
                upgrade_timing='before')
        return self._create_check_result('U2', True, [],
            f'Target version {target} (current: {current}). Note: EKS only supports upgrading one minor version at a time.')

    async def _check_multi_hop(self, sd: Dict) -> Dict:
        """U3: Detect multi-version hop."""
        c_maj, c_min = _parse_k8s_version(sd['current_version'])
        t_maj, t_min = _parse_k8s_version(sd['target_version'])
        hops = t_min - c_min
        if hops <= 1:
            return self._create_check_result('U3', True, [],
                f'Single version upgrade: {sd["current_version"]} -> {sd["target_version"]}.')
        steps = [f'{c_maj}.{c_min + i} -> {c_maj}.{c_min + i + 1}' for i in range(hops)]
        return self._create_check_result('U3', False,
            steps,
            f'Multi-version hop detected: {hops} sequential upgrades required. '
            f'EKS only supports one minor version at a time. '
            f'Steps: {" -> ".join([sd["current_version"]] + [f"{c_maj}.{c_min+i+1}" for i in range(hops)])}',
            upgrade_timing='before')

    # ── Upgrade Insights (U4) ───────────────────

    async def _check_upgrade_insights(self, sd: Dict) -> Dict:
        """U4: EKS Upgrade Insights passthrough with detail drill-down."""
        insights = sd.get('insights', [])
        details_map = sd.get('insight_details', {})

        if not insights:
            return self._create_check_result('U4', False, [],
                'EKS Upgrade Insights API returned no data. '
                'This may indicate the API is not accessible or audit logging is not enabled. '
                'Run `aws eks list-insights --cluster-name <cluster> --filter categories=UPGRADE_READINESS` to verify.',
                upgrade_timing='before')

        non_passing = [i for i in insights if i.get('insightStatus', {}).get('status') != 'PASSING']
        if not non_passing:
            return self._create_check_result('U4', True, [],
                f'All {len(insights)} EKS upgrade insight checks are PASSING.')

        impacted = []
        detail_parts = [f'{len(non_passing)} of {len(insights)} upgrade insight checks are not passing:']
        for ins in non_passing:
            status = ins.get('insightStatus', {})
            name = ins.get('name', 'Unknown')
            reason = status.get('reason', '')
            detail_parts.append(f'  [{status.get("status", "?")}] {name}: {reason}')

            # Add detailed info if available
            full = details_map.get(ins.get('id', ''), {})
            # Deprecation details
            for dep in full.get('categorySpecificSummary', {}).get('deprecationDetails', []):
                usage = dep.get('usage', '')
                replacement = dep.get('replacedWith', '')
                stop_ver = dep.get('stopServingVersion', '')
                detail_parts.append(f'    Deprecated: {usage} -> {replacement} (removed in {stop_ver})')
                for client in dep.get('clientStats', []):
                    ua = client.get('userAgent', '')
                    count = client.get('numberOfRequestsLast30Days', 0)
                    detail_parts.append(f'      Client: {ua} ({count} requests in 30d)')
                impacted.append(f'{usage} (removed in {stop_ver})')

            # Addon compatibility details
            for addon in full.get('categorySpecificSummary', {}).get('addonCompatibilityDetails', []):
                addon_name = addon.get('name', '')
                compat = addon.get('compatibleVersions', [])
                detail_parts.append(f'    Addon {addon_name}: compatible versions for target: {", ".join(compat[:5])}')
                impacted.append(f'addon:{addon_name}')

            # Resource-level details
            for res in full.get('resources', []):
                res_status = res.get('insightStatus', {}).get('status', '')
                res_reason = res.get('insightStatus', {}).get('reason', '')
                res_uri = res.get('kubernetesResourceUri', '') or res.get('arn', '')
                if res_status != 'PASSING':
                    detail_parts.append(f'    Resource: {res_uri} [{res_status}] {res_reason}')
                    impacted.append(res_uri)

        return self._create_check_result('U4', False, impacted,
            '\n'.join(detail_parts), upgrade_timing='before')

    # ── Addon Compatibility (U5-U7) ─────────────

    async def _check_addon_compat(self, sd: Dict) -> Dict:
        """U5: EKS managed addon version compatibility with target."""
        addon_details = sd.get('addon_details', {})
        target_versions = sd.get('addon_target_versions', {})
        target = sd['target_version']

        if not addon_details:
            return self._create_check_result('U5', True, [],
                'No EKS managed addons installed.')

        impacted = []
        details = []
        for name, addon in addon_details.items():
            current_ver = addon.get('addonVersion', 'unknown')
            target_info = target_versions.get(name, {})
            compat_versions = []
            default_ver = ''
            for av in target_info.get('addonVersions', []):
                for c in av.get('compatibilities', []):
                    if c.get('clusterVersion') == target:
                        compat_versions.append(av.get('addonVersion', ''))
                        if c.get('defaultVersion'):
                            default_ver = av.get('addonVersion', '')

            if not compat_versions:
                details.append(f'{name}: current {current_ver} — no compatible versions found for K8s {target}')
                impacted.append(f'{name} {current_ver}')
            elif current_ver in compat_versions:
                details.append(f'{name}: current {current_ver} is compatible with K8s {target}')
            else:
                details.append(
                    f'{name}: current {current_ver} NOT compatible with K8s {target}. '
                    f'Default: {default_ver or "N/A"}. Latest compatible: {compat_versions[0] if compat_versions else "N/A"}'
                )
                impacted.append(f'{name} {current_ver} -> {compat_versions[0] if compat_versions else "?"}')

        compliant = len(impacted) == 0
        return self._create_check_result('U5', compliant, impacted,
            '\n'.join(details), upgrade_timing='before')

    async def _check_self_managed_addons(self, sd: Dict) -> Dict:
        """U6: Detect core addons not managed by EKS addon system and return their versions.

        Returns detected self-managed addons with versions. The agent decides compatibility.
        """
        target = sd['target_version']
        managed = set(sd.get('addon_names', []))
        self_managed = []

        ks_deps = sd.get('kube_system_deployments', [])
        ks_ds = sd.get('kube_system_daemonsets', [])

        def _extract_version(workload) -> str:
            spec = workload.spec if hasattr(workload, 'spec') else None
            if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
                for c in (spec.template.spec.containers or []):
                    if c.image and ':' in c.image:
                        return c.image.split(':')[-1].lstrip('v')
            return 'unknown'

        # Check for CoreDNS deployment
        if 'coredns' not in managed:
            for dep in ks_deps:
                name = dep.metadata.name if hasattr(dep, 'metadata') else ''
                if 'coredns' in name.lower():
                    ver = _extract_version(dep)
                    self_managed.append(f'coredns:{ver} (deployment: {name}, target_k8s: {target})')
                    break

        # Check for kube-proxy daemonset
        if 'kube-proxy' not in managed:
            for ds in ks_ds:
                name = ds.metadata.name if hasattr(ds, 'metadata') else ''
                if 'kube-proxy' in name.lower():
                    ver = _extract_version(ds)
                    self_managed.append(f'kube-proxy:{ver} (daemonset: {name}, target_k8s: {target})')
                    break

        # Check for VPC CNI daemonset
        if 'vpc-cni' not in managed:
            for ds in ks_ds:
                name = ds.metadata.name if hasattr(ds, 'metadata') else ''
                if 'aws-node' in name.lower():
                    ver = _extract_version(ds)
                    self_managed.append(f'vpc-cni:{ver} (daemonset: {name}, target_k8s: {target})')
                    break

        if not self_managed:
            return self._create_check_result('U6', True, [],
                'All core addons are managed by EKS addon system.')

        return self._create_check_result('U6', False, self_managed,
            f'Found {len(self_managed)} core addon(s) installed outside EKS addon system. '
            f'Agent must verify version compatibility with K8s {target}.',
            upgrade_timing='before')

    async def _check_addon_health(self, sd: Dict) -> Dict:
        """U7: Check addon health issues."""
        addon_details = sd.get('addon_details', {})
        unhealthy = []
        for name, addon in addon_details.items():
            health = addon.get('health', {})
            issues = health.get('issues', [])
            for issue in issues:
                code = issue.get('code', '')
                msg = issue.get('message', '')
                unhealthy.append(f'{name}: [{code}] {msg}')

        if not unhealthy:
            return self._create_check_result('U7', True, [],
                f'All {len(addon_details)} EKS addons are healthy.')

        return self._create_check_result('U7', False, unhealthy,
            f'{len(unhealthy)} addon health issue(s) found. Resolve before upgrading.',
            upgrade_timing='before')

    # ── Data Plane Readiness (U8-U11) ───────────

    async def _check_kubelet_skew(self, sd: Dict) -> Dict:
        """U8: Node kubelet version skew after upgrade."""
        target = sd['target_version']
        t_maj, t_min = _parse_k8s_version(target)
        nodes = sd.get('nodes', [])
        if not nodes:
            return self._create_check_result('U8', True, [], 'No nodes found.')

        skewed = []
        for node in nodes:
            name = node.metadata.name if hasattr(node, 'metadata') else 'unknown'
            kubelet_ver = ''
            if hasattr(node, 'status') and hasattr(node.status, 'nodeInfo'):
                kubelet_ver = node.status.nodeInfo.kubeletVersion or ''
            k_maj, k_min = _parse_k8s_version(kubelet_ver)
            if k_min == 0:
                skewed.append(f'{name} (kubelet version unknown: {kubelet_ver!r})')
                continue
            # After upgrade, control plane will be at target. Kubelet must be within 3 minor versions.
            if (t_min - k_min) > 3:
                skewed.append(f'{name} (kubelet {kubelet_ver}, target control plane {target}, skew={t_min - k_min})')
            elif (t_min - k_min) > 1:
                skewed.append(f'{name} (kubelet {kubelet_ver}, will be {t_min - k_min} versions behind after upgrade)')

        if not skewed:
            return self._create_check_result('U8', True, [],
                f'All {len(nodes)} nodes have kubelet versions compatible with target {target}.')

        # Check if any exceed the 3-version limit (blocker)
        blockers = [s for s in skewed if 'skew=' in s]
        if blockers:
            return self._create_check_result('U8', False, skewed,
                f'{len(blockers)} node(s) will violate the kubelet version skew policy after upgrade. '
                'Update these nodes before upgrading the control plane.',
                upgrade_timing='before')

        return self._create_check_result('U8', False, skewed,
            f'{len(skewed)} node(s) will have version skew after upgrade. '
            'Recommended to update nodes to match the control plane version.',
            upgrade_timing='after')

    async def _check_ami_type(self, sd: Dict) -> Dict:
        """U9: AL2 AMI deprecation check."""
        target = sd['target_version']
        _, t_min = _parse_k8s_version(target)
        ng_details = sd.get('nodegroup_details', {})
        if not ng_details:
            return self._create_check_result('U9', True, [], 'No managed node groups found.')

        al2_groups = []
        for ng_name, ng in ng_details.items():
            ami_type = ng.get('amiType', '')
            if ami_type.startswith('AL2_') and not ami_type.startswith('AL2023'):
                al2_groups.append(f'{ng_name} (AMI type: {ami_type})')

        if not al2_groups:
            return self._create_check_result('U9', True, [],
                f'All managed node groups use supported AMI types.')

        if t_min >= 33:
            return self._create_check_result('U9', False, al2_groups,
                f'{len(al2_groups)} node group(s) use AL2 AMI which is REMOVED in K8s 1.33+. '
                'Migrate to AL2023 or Bottlerocket before upgrading.',
                upgrade_timing='before')

        return self._create_check_result('U9', False, al2_groups,
            f'{len(al2_groups)} node group(s) use AL2 AMI which is DEPRECATED in K8s 1.32. '
            'Plan migration to AL2023 or Bottlerocket.',
            upgrade_timing='before')

    async def _check_nodegroup_pending(self, sd: Dict) -> Dict:
        """U10: Pending launch template updates."""
        ng_details = sd.get('nodegroup_details', {})
        pending = []
        for ng_name, ng in ng_details.items():
            update_config = ng.get('updateConfig', {})
            release_ver = ng.get('releaseVersion', '')
            # Check if the node group has a pending update
            if ng.get('status') == 'DEGRADED':
                pending.append(f'{ng_name} (status: DEGRADED)')
            health = ng.get('health', {})
            for issue in health.get('issues', []):
                pending.append(f'{ng_name}: {issue.get("code", "")} - {issue.get("message", "")}')

        if not pending:
            return self._create_check_result('U10', True, [],
                f'No pending updates on {len(ng_details)} managed node group(s).')

        return self._create_check_result('U10', False, pending,
            f'{len(pending)} node group issue(s) found. Resolve before upgrading.',
            upgrade_timing='before')

    async def _check_subnet_ips(self, sd: Dict) -> Dict:
        """U11: Subnet IP availability for control plane ENIs."""
        subnets = sd.get('subnets', [])
        if not subnets:
            return self._create_check_result('U11', False, [],
                'No subnet information available. Could not verify IP availability. '
                'Run `aws ec2 describe-subnets --subnet-ids <ids>` to check manually.',
                upgrade_timing='before')

        low_ip = []
        for subnet in subnets:
            available = subnet.get('AvailableIpAddressCount', 0)
            subnet_id = subnet.get('SubnetId', '')
            az = subnet.get('AvailabilityZone', '')
            if available < 5:
                low_ip.append(f'{subnet_id} ({az}): {available} IPs available (need >= 5)')

        if not low_ip:
            return self._create_check_result('U11', True, [],
                f'All {len(subnets)} cluster subnets have sufficient IPs for upgrade.')

        return self._create_check_result('U11', False, low_ip,
            f'{len(low_ip)} subnet(s) have fewer than 5 available IPs. '
            'EKS requires at least 5 free IPs per subnet for control plane ENIs during upgrade.',
            upgrade_timing='before')

    # ── Workload Readiness (U12-U17) ────────────

    async def _check_pdb_coverage(self, sd: Dict) -> Dict:
        """U12: PDB coverage for workloads."""
        deployments = sd.get('deployments', [])
        statefulsets = sd.get('statefulsets', [])
        pdbs = sd.get('pdbs', [])

        # Build PDB selector map
        pdb_selectors = []
        for pdb in pdbs:
            spec = pdb.spec if hasattr(pdb, 'spec') else None
            if spec and hasattr(spec, 'selector') and spec.selector:
                labels = {}
                if hasattr(spec.selector, 'matchLabels') and spec.selector.matchLabels:
                    labels = dict(spec.selector.matchLabels)
                pdb_selectors.append(labels)

        uncovered = []
        deployment_ids = {id(d) for d in deployments}
        for workload in list(deployments) + list(statefulsets):
            meta = workload.metadata if hasattr(workload, 'metadata') else None
            if not meta:
                continue
            name = meta.name or ''
            ns = meta.namespace or 'default'
            # Skip kube-system workloads
            if ns == 'kube-system':
                continue
            spec = workload.spec if hasattr(workload, 'spec') else None
            if not spec:
                continue
            replicas = spec.replicas if hasattr(spec, 'replicas') else 1
            if replicas and replicas < 2:
                continue  # Single replica checked in U13

            # Check if any PDB covers this workload
            tmpl = spec.template if hasattr(spec, 'template') else None
            pod_labels = {}
            if tmpl and hasattr(tmpl, 'metadata') and tmpl.metadata and hasattr(tmpl.metadata, 'labels'):
                pod_labels = dict(tmpl.metadata.labels) if tmpl.metadata.labels else {}

            covered = False
            for pdb_labels in pdb_selectors:
                if pdb_labels and all(pod_labels.get(k) == v for k, v in pdb_labels.items()):
                    covered = True
                    break

            if not covered and replicas and replicas >= 2:
                kind = 'Deployment' if id(workload) in deployment_ids else 'StatefulSet'
                uncovered.append(f'{ns}/{name} ({kind}, {replicas} replicas)')

        if not uncovered:
            return self._create_check_result('U12', True, [],
                'All multi-replica workloads have PodDisruptionBudget coverage.')

        return self._create_check_result('U12', False, uncovered,
            f'{len(uncovered)} workload(s) with multiple replicas lack PodDisruptionBudget coverage. '
            'Without PDBs, all pods can be evicted simultaneously during node rolling updates.',
            upgrade_timing='before')

    async def _check_single_replica(self, sd: Dict) -> Dict:
        """U13: Single-replica workloads."""
        deployments = sd.get('deployments', [])
        statefulsets = sd.get('statefulsets', [])
        singles = []

        deployment_ids = {id(d) for d in deployments}
        for workload in list(deployments) + list(statefulsets):
            meta = workload.metadata if hasattr(workload, 'metadata') else None
            if not meta:
                continue
            ns = meta.namespace or 'default'
            if ns == 'kube-system':
                continue
            spec = workload.spec if hasattr(workload, 'spec') else None
            replicas = spec.replicas if spec and hasattr(spec, 'replicas') else 1
            if replicas == 1:
                kind = 'Deployment' if id(workload) in deployment_ids else 'StatefulSet'
                singles.append(f'{ns}/{meta.name} ({kind})')

        if not singles:
            return self._create_check_result('U13', True, [],
                'No single-replica workloads found outside kube-system.')

        return self._create_check_result('U13', False, singles,
            f'{len(singles)} workload(s) have only 1 replica. '
            'These will have guaranteed downtime during node replacement.',
            upgrade_timing='before')

    async def _check_readiness_probes(self, sd: Dict) -> Dict:
        """U14: Missing readiness probes."""
        pods = sd.get('pods', [])
        missing = []
        seen = set()

        for pod in pods:
            meta = pod.metadata if hasattr(pod, 'metadata') else None
            if not meta:
                continue
            ns = meta.namespace or 'default'
            if ns == 'kube-system':
                continue
            spec = pod.spec if hasattr(pod, 'spec') else None
            if not spec or not hasattr(spec, 'containers'):
                continue
            for container in (spec.containers or []):
                if not hasattr(container, 'readinessProbe') or not container.readinessProbe:
                    # Deduplicate by owner
                    owner_key = f'{ns}/{meta.name}'
                    if hasattr(meta, 'ownerReferences') and meta.ownerReferences:
                        owner = meta.ownerReferences[0]
                        owner_key = f'{ns}/{owner.kind}/{owner.name}'
                    if owner_key not in seen:
                        seen.add(owner_key)
                        missing.append(f'{owner_key} container:{container.name}')

        if not missing:
            return self._create_check_result('U14', True, [],
                'All containers have readiness probes configured.')

        # Cap the list to avoid huge output
        display = missing[:20]
        suffix = f' (and {len(missing) - 20} more)' if len(missing) > 20 else ''
        return self._create_check_result('U14', False, display,
            f'{len(missing)} container(s) missing readiness probes{suffix}. '
            'Traffic may be routed to unready pods after node replacement.',
            upgrade_timing='before')

    async def _check_topology(self, sd: Dict) -> Dict:
        """U15: Pod topology spread / anti-affinity."""
        deployments = sd.get('deployments', [])
        at_risk = []

        for dep in deployments:
            meta = dep.metadata if hasattr(dep, 'metadata') else None
            if not meta:
                continue
            ns = meta.namespace or 'default'
            if ns == 'kube-system':
                continue
            spec = dep.spec if hasattr(dep, 'spec') else None
            if not spec:
                continue
            replicas = spec.replicas if hasattr(spec, 'replicas') else 1
            if not replicas or replicas < 2:
                continue

            tmpl_spec = spec.template.spec if hasattr(spec, 'template') and hasattr(spec.template, 'spec') else None
            if not tmpl_spec:
                continue

            has_tsc = hasattr(tmpl_spec, 'topologySpreadConstraints') and tmpl_spec.topologySpreadConstraints
            has_anti = False
            if hasattr(tmpl_spec, 'affinity') and tmpl_spec.affinity:
                if hasattr(tmpl_spec.affinity, 'podAntiAffinity') and tmpl_spec.affinity.podAntiAffinity:
                    has_anti = True

            if not has_tsc and not has_anti:
                at_risk.append(f'{ns}/{meta.name} ({replicas} replicas)')

        if not at_risk:
            return self._create_check_result('U15', True, [],
                'All multi-replica deployments have topology spread or anti-affinity.')

        display = at_risk[:15]
        return self._create_check_result('U15', False, display,
            f'{len(at_risk)} deployment(s) lack topology spread constraints or pod anti-affinity. '
            'All replicas could be scheduled on the same node.',
            upgrade_timing='before')

    async def _check_min_ready_seconds(self, sd: Dict) -> Dict:
        """U16: StatefulSet minReadySeconds."""
        statefulsets = sd.get('statefulsets', [])
        missing = []
        for sts in statefulsets:
            meta = sts.metadata if hasattr(sts, 'metadata') else None
            if not meta:
                continue
            ns = meta.namespace or 'default'
            if ns == 'kube-system':
                continue
            spec = sts.spec if hasattr(sts, 'spec') else None
            min_ready = spec.minReadySeconds if spec and hasattr(spec, 'minReadySeconds') else 0
            if not min_ready or min_ready == 0:
                missing.append(f'{ns}/{meta.name}')

        if not missing:
            return self._create_check_result('U16', True, [],
                'All StatefulSets have minReadySeconds > 0.')

        return self._create_check_result('U16', False, missing,
            f'{len(missing)} StatefulSet(s) have minReadySeconds=0. '
            'This can cause premature readiness during rolling updates.',
            upgrade_timing='before')

    async def _check_termination_grace(self, sd: Dict) -> Dict:
        """U17: StatefulSet terminationGracePeriodSeconds = 0."""
        statefulsets = sd.get('statefulsets', [])
        unsafe = []
        for sts in statefulsets:
            meta = sts.metadata if hasattr(sts, 'metadata') else None
            if not meta:
                continue
            spec = sts.spec if hasattr(sts, 'spec') else None
            tmpl_spec = None
            if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
                tmpl_spec = spec.template.spec
            if tmpl_spec and hasattr(tmpl_spec, 'terminationGracePeriodSeconds'):
                if tmpl_spec.terminationGracePeriodSeconds == 0:
                    ns = meta.namespace or 'default'
                    unsafe.append(f'{ns}/{meta.name}')

        if not unsafe:
            return self._create_check_result('U17', True, [],
                'No StatefulSets with terminationGracePeriodSeconds=0.')

        return self._create_check_result('U17', False, unsafe,
            f'{len(unsafe)} StatefulSet(s) have terminationGracePeriodSeconds=0. '
            'This is unsafe and can cause data loss during upgrades.',
            upgrade_timing='before')

    # ── Third-Party Components (U18-U20) ────────

    async def _check_cluster_autoscaler(self, sd: Dict) -> Dict:
        """U18: Cluster Autoscaler version compatibility."""
        target = sd['target_version']
        ks_deps = sd.get('kube_system_deployments', [])

        ca_dep = None
        for dep in ks_deps:
            name = dep.metadata.name if hasattr(dep, 'metadata') else ''
            if 'cluster-autoscaler' in name.lower():
                ca_dep = dep
                break

        if not ca_dep:
            return self._create_check_result('U18', True, [],
                'Cluster Autoscaler not detected in kube-system.')

        # Extract image tag
        image = ''
        spec = ca_dep.spec if hasattr(ca_dep, 'spec') else None
        if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
            for c in (spec.template.spec.containers or []):
                if 'cluster-autoscaler' in (c.image or ''):
                    image = c.image
                    break

        ca_version = ''
        if ':' in image:
            ca_version = image.split(':')[-1].lstrip('v')

        if not ca_version:
            return self._create_check_result('U18', False, [f'image: {image}'],
                'Could not determine Cluster Autoscaler version from image tag.',
                upgrade_timing='after')

        # CA version should match K8s minor version
        ca_maj, ca_min = _parse_k8s_version(ca_version)
        t_maj, t_min = _parse_k8s_version(target)
        if ca_min == t_min:
            return self._create_check_result('U18', True, [],
                f'Cluster Autoscaler {ca_version} matches target K8s {target}.')

        return self._create_check_result('U18', False,
            [f'cluster-autoscaler:{ca_version} (target K8s: {target})'],
            f'Cluster Autoscaler version {ca_version} does not match target K8s {target}. '
            f'Update to v{t_maj}.{t_min}.x after upgrading the control plane.',
            upgrade_timing='after')

    async def _check_karpenter(self, sd: Dict) -> Dict:
        """U19: Karpenter version detection.

        Returns detected version and image. The agent decides compatibility.
        """
        target = sd['target_version']
        ks_deps = sd.get('kube_system_deployments', [])
        karp_deps = sd.get('karpenter_deployments', [])

        karp_dep = None
        for dep in list(ks_deps) + list(karp_deps):
            name = dep.metadata.name if hasattr(dep, 'metadata') else ''
            if 'karpenter' in name.lower():
                karp_dep = dep
                break

        if not karp_dep:
            return self._create_check_result('U19', True, [],
                'Karpenter not detected.')

        image = ''
        ns = ''
        spec = karp_dep.spec if hasattr(karp_dep, 'spec') else None
        if hasattr(karp_dep, 'metadata') and karp_dep.metadata:
            ns = karp_dep.metadata.namespace or ''
        if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
            for c in (spec.template.spec.containers or []):
                if 'karpenter' in (c.image or ''):
                    image = c.image
                    break

        version = image.split(':')[-1].lstrip('v') if ':' in image else 'unknown'

        return self._create_check_result('U19', False,
            [f'karpenter:{version} (namespace: {ns}, image: {image}, target_k8s: {target})'],
            f'Karpenter {version} detected. Agent must verify compatibility with K8s {target}.',
            upgrade_timing='before')

    async def _check_third_party_inventory(self, sd: Dict) -> Dict:
        """U20: Scan for known third-party controllers and return their versions.

        Returns detected components with versions. The agent decides compatibility.
        """
        target = sd['target_version']
        ks_deps = sd.get('kube_system_deployments', [])
        ks_ds = sd.get('kube_system_daemonsets', [])

        known_patterns = {
            'cert-manager': ['cert-manager'],
            'istio': ['istiod', 'istio'],
            'argocd': ['argocd', 'argo-cd'],
            'external-secrets': ['external-secrets'],
            'external-dns': ['external-dns'],
            'metrics-server': ['metrics-server'],
            'aws-load-balancer-controller': ['aws-load-balancer'],
            'ingress-nginx': ['ingress-nginx'],
            'prometheus': ['prometheus'],
            'grafana': ['grafana'],
            'velero': ['velero'],
            'flux': ['flux', 'source-controller', 'kustomize-controller'],
        }

        found = {}
        all_workloads = list(ks_deps) + list(ks_ds) + list(sd.get('third_party_workloads', []))

        for workload in all_workloads:
            meta = workload.metadata if hasattr(workload, 'metadata') else None
            if not meta:
                continue
            wl_name = (meta.name or '').lower()
            ns = meta.namespace or ''

            for component, patterns in known_patterns.items():
                if component in found:
                    continue
                if any(p in wl_name for p in patterns):
                    image = ''
                    spec = workload.spec if hasattr(workload, 'spec') else None
                    if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
                        for c in (spec.template.spec.containers or []):
                            if c.image:
                                image = c.image
                                break
                    version = image.split(':')[-1].lstrip('v') if ':' in image else 'unknown'
                    found[component] = {
                        'version': version,
                        'namespace': ns,
                        'workload': meta.name,
                        'image': image,
                    }

        if not found:
            return self._create_check_result('U20', True, [],
                'No known third-party controllers detected.')

        impacted = [
            f'{name}:{info["version"]} (ns: {info["namespace"]}, image: {info["image"]}, target_k8s: {target})'
            for name, info in found.items()
        ]

        return self._create_check_result('U20', False, impacted,
            f'Found {len(found)} third-party component(s). '
            f'Agent must verify each component\'s compatibility with K8s {target}.',
            upgrade_timing='before')

    # ── Cluster Health (U21-U23) ────────────────

    async def _check_control_plane_logging(self, sd: Dict) -> Dict:
        """U21: Control plane audit logging enabled."""
        cluster = sd.get('cluster_info', {})
        logging_config = cluster.get('logging', {}).get('clusterLogging', [])

        audit_enabled = False
        enabled_types = []
        for log_group in logging_config:
            if log_group.get('enabled'):
                types = log_group.get('types', [])
                enabled_types.extend(types)
                if 'audit' in types:
                    audit_enabled = True

        if audit_enabled:
            return self._create_check_result('U21', True, [],
                f'Control plane logging enabled: {", ".join(enabled_types)}. '
                'Audit logs are active — Upgrade Insights can detect deprecated API usage.')

        return self._create_check_result('U21', False,
            ['audit logging disabled'],
            'Control plane audit logging is NOT enabled. '
            'EKS Upgrade Insights relies on audit logs to detect deprecated API usage. '
            'Without audit logs, deprecated API calls from controllers and operators will not be detected. '
            f'Currently enabled log types: {", ".join(enabled_types) if enabled_types else "none"}.',
            upgrade_timing='before')

    async def _check_cluster_health(self, sd: Dict) -> Dict:
        """U22: Cluster health issues."""
        cluster = sd.get('cluster_info', {})
        health = cluster.get('health', {})
        issues = health.get('issues', [])

        if not issues:
            return self._create_check_result('U22', True, [],
                'No cluster health issues reported.')

        impacted = [f'{i.get("code", "")}: {i.get("message", "")}' for i in issues]
        return self._create_check_result('U22', False, impacted,
            f'{len(issues)} cluster health issue(s) found. Resolve before upgrading.',
            upgrade_timing='before')

    async def _check_kube_proxy_skew(self, sd: Dict) -> Dict:
        """U23: kube-proxy version skew."""
        target = sd['target_version']
        t_maj, t_min = _parse_k8s_version(target)
        ks_ds = sd.get('kube_system_daemonsets', [])

        kp_version = ''
        for ds in ks_ds:
            name = ds.metadata.name if hasattr(ds, 'metadata') else ''
            if 'kube-proxy' in name.lower():
                spec = ds.spec if hasattr(ds, 'spec') else None
                if spec and hasattr(spec, 'template') and hasattr(spec.template, 'spec'):
                    for c in (spec.template.spec.containers or []):
                        if 'kube-proxy' in (c.image or ''):
                            kp_version = c.image.split(':')[-1].lstrip('v') if ':' in c.image else ''
                            break
                break

        if not kp_version:
            return self._create_check_result('U23', True, [],
                'kube-proxy version could not be determined (may be managed as EKS addon).')

        kp_maj, kp_min = _parse_k8s_version(kp_version)
        skew = t_min - kp_min

        if skew <= 1:
            return self._create_check_result('U23', True, [],
                f'kube-proxy {kp_version} is compatible with target {target}.')

        if skew > 3:
            return self._create_check_result('U23', False,
                [f'kube-proxy {kp_version} (skew={skew} with target {target})'],
                f'kube-proxy {kp_version} will be {skew} minor versions behind target {target}. '
                'Maximum allowed skew is 3. Update kube-proxy before upgrading.',
                upgrade_timing='before')

        return self._create_check_result('U23', False,
            [f'kube-proxy {kp_version} (skew={skew} with target {target})'],
            f'kube-proxy {kp_version} will be {skew} minor versions behind target {target}. '
            'Update kube-proxy after upgrading the control plane.',
            upgrade_timing='after')

    # ── API Deprecation Scan (U24-U26) ──────────

    def _get_removed_apis(self, target_version: str) -> List[Dict]:
        """Get APIs removed in or before the target version."""
        removed = []
        for entry in self.deprecation_db:
            removed_in = entry.get('removed-in', '')
            if not removed_in or entry.get('component', '') != 'k8s':
                continue
            if _version_lte(removed_in, f'v{target_version}'):
                removed.append(entry)
        return removed

    def _get_deprecated_apis(self, target_version: str) -> List[Dict]:
        """Get APIs deprecated but NOT yet removed in the target version."""
        deprecated = []
        for entry in self.deprecation_db:
            dep_in = entry.get('deprecated-in', '')
            removed_in = entry.get('removed-in', '')
            if not dep_in or entry.get('component', '') != 'k8s':
                continue
            # Deprecated in or before target, but not yet removed
            if _version_lte(dep_in, f'v{target_version}'):
                if not removed_in or not _version_lte(removed_in, f'v{target_version}'):
                    deprecated.append(entry)
        return deprecated

    def _decode_helm_release(self, secret_data: str) -> List[Dict]:
        """Decode a Helm release secret and extract manifest apiVersions."""
        try:
            # Helm stores releases as base64(gzip(base64(json)))
            raw = base64.b64decode(secret_data)
            try:
                decompressed = gzip.decompress(base64.b64decode(raw))
            except Exception:
                # Some Helm versions use single base64 + gzip
                decompressed = gzip.decompress(raw)
            release = json.loads(decompressed)
            manifest_str = release.get('manifest', '')
            if not manifest_str:
                return []

            manifests = []
            for doc in yaml.safe_load_all(manifest_str):
                if doc and isinstance(doc, dict) and 'apiVersion' in doc and 'kind' in doc:
                    manifests.append({
                        'apiVersion': doc['apiVersion'],
                        'kind': doc['kind'],
                        'name': doc.get('metadata', {}).get('name', 'unknown'),
                        'namespace': doc.get('metadata', {}).get('namespace', ''),
                    })
            return manifests
        except Exception as e:
            logger.debug(f'Failed to decode Helm release: {e}')
            return []

    async def _check_deprecated_apis_live(self, sd: Dict) -> Dict:
        """U24: Scan live resources for deprecated APIs removed in target version."""
        target = sd['target_version']
        removed_apis = self._get_removed_apis(target)
        if not removed_apis:
            return self._create_check_result('U24', True, [],
                f'No K8s APIs are removed in version {target}.')

        # Only check APIs removed in the TARGET version specifically,
        # not all historically removed APIs (those are already gone and can't be served)
        _, t_min = _parse_k8s_version(target)
        target_removed = [
            e for e in removed_apis
            if _parse_k8s_version(e.get('removed-in', ''))[1] >= t_min - 1
        ]

        if not target_removed:
            return self._create_check_result('U24', True, [],
                f'No recently removed APIs to scan for target {target}.')

        impacted = []
        k8s = sd['k8s_client']

        # Try deprecated API versions directly to see if they still serve
        for entry in target_removed:
            api_ver = entry['version']
            kind = entry.get('kind', '')
            if not kind or kind in ('', 'FlowControl'):
                continue
            try:
                resources = k8s.list_resources(kind=kind, api_version=api_ver)
                items = resources.items if hasattr(resources, 'items') else []
                for item in items:
                    name = item.metadata.name if hasattr(item, 'metadata') else 'unknown'
                    ns = item.metadata.namespace if hasattr(item, 'metadata') and hasattr(item.metadata, 'namespace') else ''
                    replacement = entry.get('replacement-api', '')
                    loc = f'{ns}/{name}' if ns else name
                    impacted.append(
                        f'{loc} {kind} {api_ver} (removed in {entry["removed-in"]}, '
                        f'replace with {replacement})'
                    )
            except Exception:
                # API version not served or not accessible — expected for removed APIs
                pass

        if not impacted:
            return self._create_check_result('U24', True, [],
                f'No live resources found using API versions removed in {target}. '
                f'Scanned {len(target_removed)} recently deprecated API entries.')

        return self._create_check_result('U24', False, impacted[:30],
            f'{len(impacted)} resource(s) using API versions removed in K8s {target}. '
            'These will fail after upgrade.',
            upgrade_timing='before')

    async def _check_deprecated_apis_helm(self, sd: Dict) -> Dict:
        """U25: Scan Helm release manifests for deprecated APIs."""
        target = sd['target_version']
        removed_apis = self._get_removed_apis(target)
        if not removed_apis:
            return self._create_check_result('U25', True, [],
                f'No K8s APIs are removed in version {target}.')

        removed_lookup = {}
        for entry in removed_apis:
            key = (entry['version'], entry.get('kind', ''))
            removed_lookup[key] = entry

        helm_secrets = sd.get('helm_secrets', [])
        if not helm_secrets:
            return self._create_check_result('U25', True, [],
                'No Helm releases found in the cluster.')

        impacted = []
        releases_scanned = 0

        for secret in helm_secrets:
            data = {}
            if hasattr(secret, 'data') and secret.data:
                data = dict(secret.data)
            release_data = data.get('release', '')
            if not release_data:
                continue

            releases_scanned += 1
            # Get release name from secret name
            secret_name = secret.metadata.name if hasattr(secret, 'metadata') else 'unknown'
            # Helm secret names: sh.helm.release.v1.<name>.v<revision>
            release_name = secret_name.replace('sh.helm.release.v1.', '').rsplit('.v', 1)[0]

            manifests = self._decode_helm_release(release_data)
            for m in manifests:
                key = (m['apiVersion'], m['kind'])
                if key in removed_lookup:
                    entry = removed_lookup[key]
                    impacted.append(
                        f'helm:{release_name}/{m["name"]} {m["kind"]} '
                        f'{m["apiVersion"]} (removed in {entry["removed-in"]}, '
                        f'replace with {entry.get("replacement-api", "N/A")})'
                    )

        if not impacted:
            return self._create_check_result('U25', True, [],
                f'Scanned {releases_scanned} Helm release(s). '
                f'No deprecated API versions found for target {target}.')

        return self._create_check_result('U25', False, impacted[:30],
            f'{len(impacted)} resource(s) in Helm releases using API versions removed in K8s {target}. '
            f'Scanned {releases_scanned} Helm release(s). '
            'Update the Helm charts to use current API versions before upgrading.',
            upgrade_timing='before')

    async def _check_deprecated_apis_warning(self, sd: Dict) -> Dict:
        """U26: Proactive warning for deprecated (but not yet removed) APIs."""
        target = sd['target_version']
        deprecated_apis = self._get_deprecated_apis(target)
        if not deprecated_apis:
            return self._create_check_result('U26', True, [],
                f'No additional API deprecation warnings for version {target}.')

        # Just report what's deprecated for awareness
        warnings = []
        for entry in deprecated_apis:
            removed_in = entry.get('removed-in', 'future')
            warnings.append(
                f'{entry["version"]} {entry.get("kind", "")} '
                f'(deprecated in {entry.get("deprecated-in", "?")}, '
                f'will be removed in {removed_in}, '
                f'replace with {entry.get("replacement-api", "N/A")})'
            )

        if not warnings:
            return self._create_check_result('U26', True, [],
                'No upcoming API deprecation warnings.')

        return self._create_check_result('U26', True, [],
            f'{len(warnings)} API version(s) are deprecated in K8s {target} but not yet removed. '
            'Plan to migrate these before they are removed in future versions:\n' +
            '\n'.join(f'  - {w}' for w in warnings[:10]),
            upgrade_timing='after')

    # ── Infrastructure Checks (U27-U31) ─────────

    async def _check_node_subnet_ips(self, sd: Dict) -> Dict:
        """U27: Node subnet IP availability for rolling replacement."""
        subnets = sd.get('subnets', [])
        ng_details = sd.get('nodegroup_details', {})
        if not subnets or not ng_details:
            return self._create_check_result('U27', True, [],
                'No subnet or node group information available.')

        # Collect subnet IDs used by node groups
        ng_subnet_ids = set()
        for ng_name, ng in ng_details.items():
            for sid in ng.get('subnets', []):
                ng_subnet_ids.add(sid)

        low_ip = []
        for subnet in subnets:
            sid = subnet.get('SubnetId', '')
            if sid not in ng_subnet_ids:
                continue
            available = subnet.get('AvailableIpAddressCount', 0)
            az = subnet.get('AvailabilityZone', '')
            # During rolling update, new nodes launch before old ones terminate
            # Need enough IPs for at least 1 extra node per subnet
            if available < 10:
                low_ip.append(f'{sid} ({az}): {available} IPs available')

        if not low_ip:
            return self._create_check_result('U27', True, [],
                'Node subnets have sufficient IPs for rolling replacement.')

        return self._create_check_result('U27', False, low_ip,
            f'{len(low_ip)} node subnet(s) have fewer than 10 available IPs. '
            'During rolling updates, new nodes launch before old ones terminate. '
            'Insufficient IPs can block new node provisioning.',
            upgrade_timing='before')

    async def _check_pod_subnet_ips(self, sd: Dict) -> Dict:
        """U28: Pod subnet IPs with custom networking."""
        cluster = sd.get('cluster_info', {})
        # Check if custom networking is enabled via VPC CNI env var
        ks_ds = sd.get('kube_system_daemonsets', [])
        custom_networking = False

        for ds in ks_ds:
            name = ds.metadata.name if hasattr(ds, 'metadata') else ''
            if 'aws-node' not in name.lower():
                continue
            spec = ds.spec if hasattr(ds, 'spec') else None
            if not spec or not hasattr(spec, 'template') or not hasattr(spec.template, 'spec'):
                continue
            for c in (spec.template.spec.containers or []):
                if 'aws-node' not in (c.name or ''):
                    continue
                for env in (c.env or []):
                    if hasattr(env, 'name') and env.name == 'AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG':
                        if hasattr(env, 'value') and env.value and env.value.lower() == 'true':
                            custom_networking = True
            break

        if not custom_networking:
            return self._create_check_result('U28', True, [],
                'VPC CNI custom networking is not enabled. Pod IPs use the same subnets as nodes.')

        # Custom networking is enabled — we can't easily determine pod subnets
        # without reading ENIConfig CRDs, so flag for manual review
        return self._create_check_result('U28', False,
            ['VPC CNI custom networking enabled'],
            'VPC CNI custom networking is enabled. Pods use different subnets than nodes. '
            'Verify that pod subnets have sufficient available IPs for the upgrade. '
            'Check ENIConfig resources for pod subnet IDs.',
            upgrade_timing='before')

    async def _check_ec2_limits(self, sd: Dict) -> Dict:
        """U29: EC2 instance service limits."""
        try:
            sq_client = AwsHelper.create_boto3_client('service-quotas')
            # On-Demand Standard instances quota
            resp = sq_client.get_service_quota(
                ServiceCode='ec2',
                QuotaCode='L-1216C47A'  # Running On-Demand Standard instances
            )
            quota = resp.get('Quota', {})
            quota_value = quota.get('Value', 0)
            # Get current usage
            usage = quota.get('UsageMetric', {})

            return self._create_check_result('U29', True, [],
                f'EC2 On-Demand Standard instance quota: {int(quota_value)} vCPUs. '
                'Verify sufficient headroom for temporary extra nodes during rolling update.')
        except Exception as e:
            return self._create_check_result('U29', True, [],
                f'Could not check EC2 service limits: {e}. '
                'Verify manually that you have headroom for extra nodes during upgrade.')

    async def _check_ebs_gp2_limits(self, sd: Dict) -> Dict:
        """U30: EBS GP2 volume service limits."""
        try:
            sq_client = AwsHelper.create_boto3_client('service-quotas')
            resp = sq_client.get_service_quota(
                ServiceCode='ebs',
                QuotaCode='L-D18FCD1D'  # Storage for General Purpose SSD (gp2) volumes
            )
            quota_value = resp.get('Quota', {}).get('Value', 0)
            return self._create_check_result('U30', True, [],
                f'EBS GP2 storage quota: {int(quota_value)} TiB. '
                'PVs backed by GP2 may need re-attachment during node replacement.')
        except Exception as e:
            return self._create_check_result('U30', True, [],
                f'Could not check EBS GP2 limits: {e}. Verify manually if using GP2 PVs.')

    async def _check_ebs_gp3_limits(self, sd: Dict) -> Dict:
        """U31: EBS GP3 volume service limits."""
        try:
            sq_client = AwsHelper.create_boto3_client('service-quotas')
            resp = sq_client.get_service_quota(
                ServiceCode='ebs',
                QuotaCode='L-7A658B76'  # Storage for General Purpose SSD (gp3) volumes
            )
            quota_value = resp.get('Quota', {}).get('Value', 0)
            return self._create_check_result('U31', True, [],
                f'EBS GP3 storage quota: {int(quota_value)} TiB. '
                'PVs backed by GP3 may need re-attachment during node replacement.')
        except Exception as e:
            return self._create_check_result('U31', True, [],
                f'Could not check EBS GP3 limits: {e}. Verify manually if using GP3 PVs.')

    # ── Kubernetes Compatibility Checks (U32-U38) ─

    async def _check_self_managed_ng_updates(self, sd: Dict) -> Dict:
        """U32: Self-managed node group launch template updates."""
        # Self-managed node groups are ASGs not managed by EKS
        # We detect them by finding nodes not belonging to any managed node group
        nodes = sd.get('nodes', [])
        ng_details = sd.get('nodegroup_details', {})

        managed_node_names = set()
        for ng_name, ng in ng_details.items():
            # Managed node groups label their nodes
            pass  # We can't easily get managed node names without more API calls

        # Check for nodes with eks.amazonaws.com/nodegroup label missing
        self_managed = []
        for node in nodes:
            labels = {}
            if hasattr(node, 'metadata') and hasattr(node.metadata, 'labels') and node.metadata.labels:
                labels = dict(node.metadata.labels)
            if 'eks.amazonaws.com/nodegroup' not in labels:
                name = node.metadata.name if hasattr(node, 'metadata') else 'unknown'
                self_managed.append(name)

        if not self_managed:
            return self._create_check_result('U32', True, [],
                'No self-managed nodes detected. All nodes belong to EKS managed node groups.')

        return self._create_check_result('U32', False, self_managed[:15],
            f'{len(self_managed)} self-managed node(s) detected. '
            'These nodes are not automatically upgraded by EKS. '
            'You must manually update their AMIs and replace them after upgrading the control plane.',
            upgrade_timing='after')

    async def _check_kube_proxy_ipvs(self, sd: Dict) -> Dict:
        """U33: kube-proxy IPVS mode deprecation (1.35+)."""
        target = sd['target_version']
        _, t_min = _parse_k8s_version(target)

        if t_min < 35:
            return self._create_check_result('U33', True, [],
                f'kube-proxy IPVS deprecation not applicable for target {target} (affects 1.35+).')

        # Check kube-proxy configmap for mode
        ks_cms = sd.get('kube_system_configmaps', [])

        for cm in ks_cms:
            name = cm.metadata.name if hasattr(cm, 'metadata') else ''
            if 'kube-proxy' not in name.lower():
                continue
            data = dict(cm.data) if hasattr(cm, 'data') and cm.data else {}
            config_str = data.get('config', '') or data.get('kubeconfig', '')
            if 'ipvs' in config_str.lower() and 'mode' in config_str.lower():
                # Check if mode is set to ipvs
                if re.search(r'mode:\s*["\']?ipvs', config_str, re.IGNORECASE):
                    severity_msg = 'REMOVED' if t_min >= 36 else 'DEPRECATED'
                    return self._create_check_result('U33', False,
                        ['kube-proxy mode: ipvs'],
                        f'kube-proxy is configured in IPVS mode which is {severity_msg} in K8s {target}. '
                        'Migrate to iptables or nftables proxy mode.',
                        upgrade_timing='before')

        return self._create_check_result('U33', True, [],
            'kube-proxy is not using IPVS mode.')

    async def _check_ingress_nginx_retirement(self, sd: Dict) -> Dict:
        """U34: Ingress NGINX controller retirement check."""
        target = sd['target_version']
        _, t_min = _parse_k8s_version(target)

        if t_min < 35:
            return self._create_check_result('U34', True, [],
                f'Ingress NGINX retirement not applicable for target {target} (affects 1.35+).')

        # Scan for ingress-nginx controller images
        all_workloads = (
            list(sd.get('kube_system_deployments', []))
            + list(sd.get('kube_system_daemonsets', []))
            + list(sd.get('third_party_workloads', []))
        )

        found = []
        for wl in all_workloads:
            spec = wl.spec if hasattr(wl, 'spec') else None
            if not spec or not hasattr(spec, 'template') or not hasattr(spec.template, 'spec'):
                continue
            for c in (spec.template.spec.containers or []):
                img = c.image or ''
                if 'registry.k8s.io/ingress-nginx/controller' in img or 'k8s.gcr.io/ingress-nginx/controller' in img:
                    ns = wl.metadata.namespace if hasattr(wl, 'metadata') else ''
                    name = wl.metadata.name if hasattr(wl, 'metadata') else ''
                    found.append(f'{ns}/{name} (image: {img})')

        if not found:
            return self._create_check_result('U34', True, [],
                'No retired Kubernetes community Ingress NGINX controller detected.')

        return self._create_check_result('U34', False, found,
            f'{len(found)} workload(s) use the retired Kubernetes community Ingress NGINX controller. '
            'This controller is no longer maintained. Migrate to AWS Load Balancer Controller '
            'or another actively maintained ingress controller.',
            upgrade_timing='before')

    async def _check_docker_socket(self, sd: Dict) -> Dict:
        """U35: Docker socket mounts."""
        pods = sd.get('pods', [])
        docker_mounts = []

        for pod in pods:
            meta = pod.metadata if hasattr(pod, 'metadata') else None
            if not meta:
                continue
            spec = pod.spec if hasattr(pod, 'spec') else None
            if not spec or not hasattr(spec, 'volumes'):
                continue
            for vol in (spec.volumes or []):
                hp = vol.hostPath if hasattr(vol, 'hostPath') and vol.hostPath else None
                if hp and hasattr(hp, 'path') and hp.path:
                    path = hp.path
                    if 'docker.sock' in path or 'dockershim.sock' in path:
                        ns = meta.namespace or 'default'
                        docker_mounts.append(f'{ns}/{meta.name} mounts {path}')

        if not docker_mounts:
            return self._create_check_result('U35', True, [],
                'No pods mounting docker.sock or dockershim.sock.')

        return self._create_check_result('U35', False, docker_mounts[:15],
            f'{len(docker_mounts)} pod(s) mount docker.sock or dockershim.sock. '
            'EKS uses containerd as the only container runtime (since K8s 1.24). '
            'The Docker socket does not exist on containerd nodes. '
            'These pods need to be updated to use the containerd socket or '
            'a container-runtime-agnostic approach.',
            upgrade_timing='before')

    async def _check_insufficient_replicas(self, sd: Dict) -> Dict:
        """U36: Workloads scaled to zero that may be forgotten during upgrade."""
        deployments = sd.get('deployments', [])
        statefulsets = sd.get('statefulsets', [])
        scaled_down = []

        deployment_ids = {id(d) for d in deployments}
        for wl in list(deployments) + list(statefulsets):
            meta = wl.metadata if hasattr(wl, 'metadata') else None
            if not meta:
                continue
            ns = meta.namespace or 'default'
            if ns == 'kube-system':
                continue
            spec = wl.spec if hasattr(wl, 'spec') else None
            replicas = spec.replicas if spec and hasattr(spec, 'replicas') else 1
            if replicas is not None and replicas == 0:
                kind = 'Deployment' if id(wl) in deployment_ids else 'StatefulSet'
                scaled_down.append(f'{ns}/{meta.name} ({kind}, replicas=0)')

        if not scaled_down:
            return self._create_check_result('U36', True, [],
                'No workloads scaled to zero.')

        return self._create_check_result('U36', False, scaled_down[:20],
            f'{len(scaled_down)} workload(s) are scaled to zero replicas. '
            'These may be forgotten during upgrade validation. '
            'Verify they are intentionally scaled down and test them after upgrade.',
            upgrade_timing='after')

    async def _check_misconfig_insights(self, sd: Dict) -> Dict:
        """U37: EKS MISCONFIGURATION insights."""
        insights = sd.get('misconfig_insights', [])

        non_passing = [i for i in insights if i.get('insightStatus', {}).get('status') != 'PASSING']
        if not non_passing:
            count = len(insights)
            if count == 0:
                return self._create_check_result('U37', True, [],
                    'No MISCONFIGURATION insights available.')
            return self._create_check_result('U37', True, [],
                f'All {count} configuration insight checks are PASSING.')

        impacted = []
        for ins in non_passing:
            status = ins.get('insightStatus', {})
            name = ins.get('name', 'Unknown')
            reason = status.get('reason', '')
            impacted.append(f'[{status.get("status", "?")}] {name}: {reason}')

        return self._create_check_result('U37', False, impacted,
            f'{len(non_passing)} configuration insight(s) are not passing. '
            'These misconfigurations may affect cluster health during upgrade.',
            upgrade_timing='before')

    async def _check_third_party_api_deprecations(self, sd: Dict) -> Dict:
        """U38: Third-party component API deprecations (Istio, cert-manager)."""
        # Filter deprecation DB for non-k8s components
        third_party_entries = [
            e for e in self.deprecation_db
            if e.get('component', 'k8s') != 'k8s'
        ]

        if not third_party_entries:
            return self._create_check_result('U38', True, [],
                'No third-party API deprecation entries in database.')

        # Scan Helm releases for third-party deprecated APIs
        helm_secrets = sd.get('helm_secrets', [])
        tp_lookup = {}
        for entry in third_party_entries:
            key = (entry['version'], entry.get('kind', ''))
            tp_lookup[key] = entry

        impacted = []
        for secret in helm_secrets:
            data = dict(secret.data) if hasattr(secret, 'data') and secret.data else {}
            release_data = data.get('release', '')
            if not release_data:
                continue
            secret_name = secret.metadata.name if hasattr(secret, 'metadata') else 'unknown'
            release_name = secret_name.replace('sh.helm.release.v1.', '').rsplit('.v', 1)[0]

            manifests = self._decode_helm_release(release_data)
            for m in manifests:
                key = (m['apiVersion'], m['kind'])
                if key in tp_lookup:
                    entry = tp_lookup[key]
                    impacted.append(
                        f'helm:{release_name}/{m["name"]} {m["kind"]} '
                        f'{m["apiVersion"]} ({entry["component"]}, '
                        f'removed in {entry.get("removed-in", "?")}, '
                        f'replace with {entry.get("replacement-api", "N/A")})'
                    )

        # Also scan live CRDs for third-party deprecated APIs
        for crd in sd.get('crds', []):
            crd_name = crd.metadata.name if hasattr(crd, 'metadata') else ''
            # Check if CRD belongs to a known third-party component
            for component in ['istio', 'cert-manager']:
                if component.replace('-', '') in crd_name.replace('-', '').lower():
                    spec = crd.spec if hasattr(crd, 'spec') else None
                    if spec and hasattr(spec, 'versions'):
                        for ver in (spec.versions or []):
                            ver_name = ver.name if hasattr(ver, 'name') else ''
                            # Check if any served version is deprecated
                            for entry in third_party_entries:
                                if entry.get('component') == component and ver_name in entry.get('version', ''):
                                    impacted.append(f'CRD:{crd_name} version {ver_name} ({component})')

        if not impacted:
            return self._create_check_result('U38', True, [],
                'No third-party API deprecation issues found (checked Istio, cert-manager).')

        return self._create_check_result('U38', False, impacted[:20],
            f'{len(impacted)} third-party API deprecation issue(s) found. '
            'Update the affected components to versions using current APIs.',
            upgrade_timing='before')
