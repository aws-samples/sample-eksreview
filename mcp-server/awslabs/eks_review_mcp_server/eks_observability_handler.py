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

"""EKS Observability handler for the EKS Review MCP Server.

Checks default vended EKS control plane metrics in the AWS/EKS CloudWatch
namespace (available on K8s 1.28+ at no extra cost, no add-on required)
over the last 7 days to surface operational issues.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_response
from awslabs.eks_review_mcp_server.k8s_client_cache import K8sClientCache
from awslabs.eks_review_mcp_server.models import ObservabilityCheckResponse
from loguru import logger
from mcp.server.fastmcp import Context, FastMCP
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, List, Optional


# Default lookback period in days
DEFAULT_LOOKBACK_DAYS = 7

# Minimum EKS version that vends metrics by default
MIN_METRICS_VERSION = '1.28'


class EKSObservabilityHandler:
    """Handler for EKS observability checks using default vended CloudWatch metrics."""

    def __init__(self, mcp: FastMCP, client_cache: K8sClientCache):
        """Initialize the observability handler."""
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the MCP tool
        @mcp.tool()
        async def check_eks_observability(
            ctx: Context,
            cluster_name: str = Field(
                ..., description='Name of the EKS cluster to check for observability metrics.'
            ),
            region: str = Field(
                ..., description='AWS region where the cluster is located (required).'
            ),
            lookback_days: Optional[int] = Field(
                None, description='Number of days to look back for metrics. Default is 7 days.'
            ),
        ) -> ObservabilityCheckResponse:
            """Check EKS cluster control plane observability metrics.

            This tool queries the default vended EKS control plane metrics
            in the AWS/EKS CloudWatch namespace (available on K8s 1.28+ at
            no extra cost, no add-on required) over the specified lookback
            period to surface operational issues.

            The tool evaluates 5 key metrics:
            - API server 5xx error rate (fundamental health signal)
            - API server 429 throttling (APF rejection indicator)
            - etcd storage size approaching 8GB limit
            - Admission webhook P99 latency
            - Scheduler pending pods (capacity signal)
            """
            return await self.check_eks_observability(
                ctx, cluster_name, region, lookback_days
            )

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load the observability check registry from JSON."""
        try:
            data_dir = os.path.join(os.path.dirname(__file__), 'data')
            registry_path = os.path.join(data_dir, 'eks_observability_checks.json')
            with open(registry_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f'Failed to load observability check registry: {e}')
            return {'observability_checks': {}}

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Get all observability checks from the registry."""
        return self.check_registry.get('observability_checks', {})

    def _get_check_info(self, check_id: str) -> Dict[str, Any]:
        """Get check info by ID."""
        checks = self._get_all_checks()
        return checks.get(check_id, {})

    def _create_check_result(
        self, check_id: str, compliant: bool, impacted_resources: List[str], details: str
    ) -> Dict[str, Any]:
        """Create a standardized check result."""
        check_info = self._get_check_info(check_id)
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'Medium'),
            'compliant': compliant,
            'impacted_resources': impacted_resources,
            'details': details,
        }

    def _create_check_error_result(self, check_id: str, error_msg: str) -> Dict[str, Any]:
        """Create an error check result."""
        check_info = self._get_check_info(check_id)
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'Medium'),
            'compliant': False,
            'impacted_resources': [],
            'details': f'Error: {error_msg}',
        }

    def _create_error_response(
        self, cluster_name: str, error_msg: str
    ) -> ObservabilityCheckResponse:
        """Create an error response."""
        error_text = json.dumps({
            'summary': f'Observability check failed for cluster {cluster_name}: {error_msg}',
            'overall_compliant': False,
            'passed_count': 0,
            'failed_count': 0,
            'check_results': [],
        })
        return ObservabilityCheckResponse(
            isError=True,
            content=[TextContent(type='text', text=error_text)],
            check_results=[],
            overall_compliant=False,
            summary=f'Error: {error_msg}',
        )

    async def check_eks_observability(
        self,
        ctx: Context,
        cluster_name: str,
        region: Optional[str] = None,
        lookback_days: Optional[int] = None,
    ) -> ObservabilityCheckResponse:
        """Run all observability checks against the cluster."""
        try:
            days = lookback_days or DEFAULT_LOOKBACK_DAYS
            logger.info(
                f'Starting observability check for cluster: {cluster_name}, '
                f'lookback: {days} days'
            )

            # Initialize clients
            clients = await self._initialize_clients(cluster_name, region)
            if not clients:
                return self._create_error_response(
                    cluster_name, 'Failed to initialize required clients'
                )

            # Verify cluster version supports default vended metrics
            cluster_version = clients.get('cluster_version', '')
            if not self._version_supports_metrics(cluster_version):
                return self._create_error_response(
                    cluster_name,
                    f'Cluster version {cluster_version} does not support default '
                    f'vended metrics. Requires {MIN_METRICS_VERSION}+.',
                )

            # Run all checks
            check_results = []
            all_compliant = True
            all_checks = self._get_all_checks()

            for check_id in sorted(all_checks.keys()):
                try:
                    check_config = all_checks[check_id]
                    if not check_config.get('enabled', True):
                        logger.info(f'Skipping disabled check {check_id}')
                        continue

                    logger.info(f'Running observability check {check_id}')
                    result = await self._execute_metric_check(
                        check_id, check_config, clients, cluster_name, days
                    )
                    check_results.append(result)
                    if not result['compliant']:
                        all_compliant = False
                    logger.info(
                        f'Observability check {check_id} completed: {result["compliant"]}'
                    )
                except Exception as e:
                    logger.error(f'Error in observability check {check_id}: {e}')
                    error_result = self._create_check_error_result(check_id, str(e))
                    check_results.append(error_result)
                    all_compliant = False

            # Build response
            passed_count = sum(1 for r in check_results if r['compliant'])
            failed_count = len(check_results) - passed_count
            summary = (
                f'Cluster {cluster_name} observability check ({days}-day lookback): '
                f'{passed_count} checks passed, {failed_count} checks failed'
            )

            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return ObservabilityCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in observability check: {e}')
            return self._create_error_response(cluster_name, str(e))

    # ------------------------------------------------------------------
    # Client initialization
    # ------------------------------------------------------------------

    async def _initialize_clients(
        self, cluster_name: str, region: Optional[str]
    ) -> Optional[Dict[str, Any]]:
        """Initialize AWS clients needed for observability checks."""
        try:
            clients: Dict[str, Any] = {}

            # EKS client — needed to get cluster version
            try:
                clients['eks'] = AwsHelper.create_boto3_client('eks', region_name=region)
                logger.info('Successfully initialized EKS client')
            except Exception as e:
                logger.error(f'Failed to initialize EKS client: {e}')
                return None

            # CloudWatch client — needed for metric queries
            try:
                clients['cloudwatch'] = AwsHelper.create_boto3_client(
                    'cloudwatch', region_name=region
                )
                logger.info('Successfully initialized CloudWatch client')
            except Exception as e:
                logger.error(f'Failed to initialize CloudWatch client: {e}')
                return None

            # Fetch cluster version
            try:
                response = clients['eks'].describe_cluster(name=cluster_name)
                cluster_data = response.get('cluster', {})
                clients['cluster_version'] = cluster_data.get('version', '')
                clients['cluster_name'] = cluster_name
                logger.info(
                    f'Cluster version: {clients["cluster_version"]}'
                )
            except Exception as e:
                logger.error(f'Failed to describe cluster: {e}')
                return None

            return clients

        except Exception as e:
            logger.error(f'Error initializing clients: {e}')
            return None

    # ------------------------------------------------------------------
    # Version check
    # ------------------------------------------------------------------

    @staticmethod
    def _version_supports_metrics(version_str: str) -> bool:
        """Check if the cluster version supports default vended metrics."""
        try:
            parts = version_str.split('.')
            major = int(parts[0])
            minor = int(parts[1]) if len(parts) > 1 else 0
            min_parts = MIN_METRICS_VERSION.split('.')
            min_major = int(min_parts[0])
            min_minor = int(min_parts[1]) if len(min_parts) > 1 else 0
            return (major, minor) >= (min_major, min_minor)
        except (ValueError, IndexError):
            logger.warning(f'Could not parse version: {version_str}')
            return False

    # ------------------------------------------------------------------
    # Generic metric check execution
    # ------------------------------------------------------------------

    async def _execute_metric_check(
        self,
        check_id: str,
        check_config: Dict[str, Any],
        clients: Dict[str, Any],
        cluster_name: str,
        lookback_days: int,
    ) -> Dict[str, Any]:
        """Execute a single metric-based check by querying CloudWatch."""
        try:
            cw_client = clients['cloudwatch']
            metric_name = check_config['metric_name']
            namespace = check_config['namespace']
            statistic = check_config['statistic']
            threshold = check_config['threshold']
            threshold_desc = check_config.get('threshold_description', '')

            end_time = datetime.now(timezone.utc)
            start_time = end_time - timedelta(days=lookback_days)

            # Use 1-hour periods for 7-day lookback (168 data points max)
            period = 3600

            dimensions = [
                {'Name': 'ClusterName', 'Value': cluster_name},
            ]

            # Query CloudWatch
            response = cw_client.get_metric_statistics(
                Namespace=namespace,
                MetricName=metric_name,
                Dimensions=dimensions,
                StartTime=start_time,
                EndTime=end_time,
                Period=period,
                Statistics=[statistic],
            )

            datapoints = response.get('Datapoints', [])

            if not datapoints:
                return self._create_check_result(
                    check_id,
                    True,
                    [],
                    f'No data available for {metric_name} in the last {lookback_days} days. '
                    f'Metric may not be emitting yet or cluster version may not support it.',
                )

            # Analyze datapoints based on statistic type
            return self._analyze_datapoints(
                check_id, check_config, datapoints, cluster_name, lookback_days
            )

        except Exception as e:
            logger.error(f'Error executing metric check {check_id}: {e}')
            return self._create_check_error_result(check_id, str(e))

    def _analyze_datapoints(
        self,
        check_id: str,
        check_config: Dict[str, Any],
        datapoints: List[Dict[str, Any]],
        cluster_name: str,
        lookback_days: int,
    ) -> Dict[str, Any]:
        """Analyze CloudWatch datapoints against the threshold."""
        metric_name = check_config['metric_name']
        statistic = check_config['statistic']
        threshold = check_config['threshold']
        threshold_desc = check_config.get('threshold_description', '')

        # Sort datapoints by timestamp
        sorted_dp = sorted(datapoints, key=lambda d: d['Timestamp'])

        # Extract values
        values = [dp.get(statistic, 0) for dp in sorted_dp]

        if not values:
            return self._create_check_result(
                check_id, True, [],
                f'No {statistic} values found for {metric_name}.',
            )

        # Compute aggregates
        peak_value = max(values)
        avg_value = sum(values) / len(values)
        total_value = sum(values)
        latest_value = values[-1]
        latest_ts = sorted_dp[-1]['Timestamp'].isoformat()

        # Determine breach based on statistic type
        if statistic == 'Sum':
            # For Sum metrics, check total over the period
            breached = total_value > threshold
            observed_value = total_value
            observed_label = 'total'
        elif statistic == 'Maximum':
            # For Maximum metrics, check peak
            breached = peak_value > threshold
            observed_value = peak_value
            observed_label = 'peak'
        elif statistic == 'Average':
            # For Average metrics, check the average of averages
            breached = avg_value > threshold
            observed_value = avg_value
            observed_label = 'average'
        else:
            breached = peak_value > threshold
            observed_value = peak_value
            observed_label = 'peak'

        # Format value for display
        if observed_value >= 1_000_000_000:
            display_value = f'{observed_value / 1_073_741_824:.2f} GB'
        elif observed_value >= 1_000_000:
            display_value = f'{observed_value / 1_048_576:.2f} MB'
        elif isinstance(observed_value, float):
            display_value = f'{observed_value:.4f}'
        else:
            display_value = str(int(observed_value))

        # Build details
        details = {
            'metric': metric_name,
            'lookback_days': lookback_days,
            'datapoints_count': len(values),
            'observed': display_value,
            'observed_type': observed_label,
            'threshold': threshold_desc,
            'latest_value': f'{latest_value:.4f}' if isinstance(latest_value, float) else str(int(latest_value)),
            'latest_timestamp': latest_ts,
            'peak': f'{peak_value:.4f}' if isinstance(peak_value, float) else str(int(peak_value)),
            'average': f'{avg_value:.4f}' if isinstance(avg_value, float) else str(int(avg_value)),
        }

        if breached:
            return self._create_check_result(
                check_id,
                False,
                [cluster_name],
                json.dumps(details),
            )
        else:
            return self._create_check_result(
                check_id,
                True,
                [],
                json.dumps(details),
            )
