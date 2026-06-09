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
# ruff: noqa: D101, D102, D103
"""Tests for the EKSObservabilityHandler class."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from awslabs.eks_review_mcp_server.eks_observability_handler import EKSObservabilityHandler
from awslabs.eks_review_mcp_server.models import ObservabilityCheckResponse
from mcp.server.fastmcp import Context
from unittest.mock import MagicMock, AsyncMock, patch


@pytest.fixture
def mock_context():
    """Create a mock MCP context."""
    ctx = MagicMock(spec=Context)
    ctx.request_id = 'test-request-id'
    return ctx


@pytest.fixture
def mock_mcp():
    """Create a mock MCP server."""
    return MagicMock()


@pytest.fixture
def mock_client_cache():
    """Create a mock K8sClientCache."""
    cache = MagicMock()
    return cache


def _make_datapoints(values, statistic='Sum', hours_back=168):
    """Helper to create CloudWatch-style datapoints."""
    now = datetime.now(timezone.utc)
    return [
        {
            'Timestamp': now - timedelta(hours=hours_back - i),
            statistic: v,
        }
        for i, v in enumerate(values)
    ]


class TestEKSObservabilityHandlerInit:
    """Tests for handler initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization registers the tool."""
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        mock_mcp.tool.assert_called_once()

    def test_load_check_registry(self, mock_mcp, mock_client_cache):
        """Test that the check registry loads 5 checks."""
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        checks = handler._get_all_checks()

        assert len(checks) == 5
        assert 'OB1' in checks
        assert 'OB2' in checks
        assert 'OB3' in checks
        assert 'OB4' in checks
        assert 'OB5' in checks

    def test_check_registry_has_required_fields(self, mock_mcp, mock_client_cache):
        """Test that each check has all required fields."""
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        checks = handler._get_all_checks()

        required_fields = [
            'name', 'description', 'category', 'severity', 'enabled',
            'metric_name', 'namespace', 'statistic', 'threshold',
        ]
        for check_id, check in checks.items():
            for field in required_fields:
                assert field in check, f'{check_id} missing field: {field}'

    def test_all_checks_use_aws_eks_namespace(self, mock_mcp, mock_client_cache):
        """Test that all checks query the AWS/EKS namespace."""
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        checks = handler._get_all_checks()

        for check_id, check in checks.items():
            assert check['namespace'] == 'AWS/EKS', (
                f'{check_id} uses namespace {check["namespace"]} instead of AWS/EKS'
            )


class TestVersionSupportsMetrics:
    """Tests for the version check logic."""

    def test_version_128_supported(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('1.28') is True

    def test_version_130_supported(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('1.30') is True

    def test_version_127_not_supported(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('1.27') is False

    def test_version_124_not_supported(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('1.24') is False

    def test_version_invalid_string(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('invalid') is False

    def test_version_empty_string(self, mock_mcp, mock_client_cache):
        handler = EKSObservabilityHandler(mock_mcp, mock_client_cache)
        assert handler._version_supports_metrics('') is False


class TestAnalyzeDatapoints:
    """Tests for the datapoint analysis logic."""

    def setup_method(self):
        self.mock_mcp = MagicMock()
        self.mock_cache = MagicMock()
        self.handler = EKSObservabilityHandler(self.mock_mcp, self.mock_cache)

    def test_sum_metric_below_threshold(self):
        """Sum metric total below threshold should be compliant."""
        config = {
            'metric_name': 'apiserver_request_total_5XX',
            'statistic': 'Sum',
            'threshold': 100,
            'threshold_description': 'More than 100 errors',
        }
        datapoints = _make_datapoints([5, 10, 3, 2, 1], statistic='Sum')
        result = self.handler._analyze_datapoints(
            'OB1', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is True

    def test_sum_metric_above_threshold(self):
        """Sum metric total above threshold should be non-compliant."""
        config = {
            'metric_name': 'apiserver_request_total_5XX',
            'statistic': 'Sum',
            'threshold': 100,
            'threshold_description': 'More than 100 errors',
        }
        datapoints = _make_datapoints([30, 40, 20, 15, 10], statistic='Sum')
        result = self.handler._analyze_datapoints(
            'OB1', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    def test_maximum_metric_below_threshold(self):
        """Maximum metric peak below threshold should be compliant."""
        config = {
            'metric_name': 'apiserver_storage_size_bytes',
            'statistic': 'Maximum',
            'threshold': 6442450944,  # 6GB
            'threshold_description': 'etcd exceeded 6GB',
        }
        # 4GB peak
        datapoints = _make_datapoints(
            [3_000_000_000, 4_000_000_000, 3_500_000_000], statistic='Maximum'
        )
        result = self.handler._analyze_datapoints(
            'OB3', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is True

    def test_maximum_metric_above_threshold(self):
        """Maximum metric peak above threshold should be non-compliant."""
        config = {
            'metric_name': 'apiserver_storage_size_bytes',
            'statistic': 'Maximum',
            'threshold': 6442450944,  # 6GB
            'threshold_description': 'etcd exceeded 6GB',
        }
        # 7GB peak
        datapoints = _make_datapoints(
            [5_000_000_000, 7_000_000_000, 5_500_000_000], statistic='Maximum'
        )
        result = self.handler._analyze_datapoints(
            'OB3', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is False

    def test_average_metric_below_threshold(self):
        """Average metric below threshold should be compliant."""
        config = {
            'metric_name': 'apiserver_admission_webhook_admission_duration_seconds',
            'statistic': 'Average',
            'threshold': 3.0,
            'threshold_description': 'Webhook P99 exceeded 3s',
        }
        datapoints = _make_datapoints([0.5, 1.0, 0.8, 0.3], statistic='Average')
        result = self.handler._analyze_datapoints(
            'OB4', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is True

    def test_average_metric_above_threshold(self):
        """Average metric above threshold should be non-compliant."""
        config = {
            'metric_name': 'apiserver_admission_webhook_admission_duration_seconds',
            'statistic': 'Average',
            'threshold': 3.0,
            'threshold_description': 'Webhook P99 exceeded 3s',
        }
        datapoints = _make_datapoints([4.0, 5.0, 3.5, 4.5], statistic='Average')
        result = self.handler._analyze_datapoints(
            'OB4', config, datapoints, 'test-cluster', 7
        )
        assert result['compliant'] is False

    def test_details_contain_metric_info(self):
        """Check result details should contain metric metadata."""
        config = {
            'metric_name': 'scheduler_pending_pods',
            'statistic': 'Maximum',
            'threshold': 10,
            'threshold_description': 'More than 10 pending pods',
        }
        datapoints = _make_datapoints([2, 5, 3], statistic='Maximum')
        result = self.handler._analyze_datapoints(
            'OB5', config, datapoints, 'test-cluster', 7
        )
        details = json.loads(result['details'])
        assert details['metric'] == 'scheduler_pending_pods'
        assert details['lookback_days'] == 7
        assert details['datapoints_count'] == 3

    def test_gb_display_formatting(self):
        """Large byte values should be formatted as GB."""
        config = {
            'metric_name': 'apiserver_storage_size_bytes',
            'statistic': 'Maximum',
            'threshold': 6442450944,
            'threshold_description': 'etcd exceeded 6GB',
        }
        # 7GB
        datapoints = _make_datapoints([7_516_192_768], statistic='Maximum')
        result = self.handler._analyze_datapoints(
            'OB3', config, datapoints, 'test-cluster', 7
        )
        details = json.loads(result['details'])
        assert 'GB' in details['observed']
        assert details['observed_type'] == 'peak'
        assert result['compliant'] is False


class TestInitializeClients:
    """Tests for client initialization."""

    def setup_method(self):
        self.mock_mcp = MagicMock()
        self.mock_cache = MagicMock()
        self.handler = EKSObservabilityHandler(self.mock_mcp, self.mock_cache)

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_initialize_clients_success(self, mock_aws_helper):
        """Test successful client initialization."""
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.return_value = {
            'cluster': {'version': '1.30'}
        }

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client

        clients = await self.handler._initialize_clients('test-cluster', None)

        assert clients is not None
        assert clients['eks'] == mock_eks
        assert clients['cloudwatch'] == mock_cw
        assert clients['cluster_version'] == '1.30'

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_initialize_clients_eks_failure(self, mock_aws_helper):
        """Test client initialization when EKS client fails."""
        mock_aws_helper.create_boto3_client.side_effect = Exception('No credentials')

        clients = await self.handler._initialize_clients('test-cluster', None)
        assert clients is None

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_initialize_clients_describe_cluster_failure(self, mock_aws_helper):
        """Test client initialization when describe_cluster fails."""
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.side_effect = Exception('Cluster not found')

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client

        clients = await self.handler._initialize_clients('bad-cluster', None)
        assert clients is None


class TestExecuteMetricCheck:
    """Tests for the CloudWatch metric query execution."""

    def setup_method(self):
        self.mock_mcp = MagicMock()
        self.mock_cache = MagicMock()
        self.handler = EKSObservabilityHandler(self.mock_mcp, self.mock_cache)

    @pytest.mark.asyncio
    async def test_execute_metric_check_no_datapoints(self):
        """Test metric check when CloudWatch returns no data."""
        mock_cw = MagicMock()
        mock_cw.get_metric_statistics.return_value = {'Datapoints': []}

        clients = {'cloudwatch': mock_cw}
        check_config = {
            'metric_name': 'apiserver_request_total_5XX',
            'namespace': 'AWS/EKS',
            'statistic': 'Sum',
            'threshold': 100,
            'threshold_description': 'test',
        }

        result = await self.handler._execute_metric_check(
            'OB1', check_config, clients, 'test-cluster', 7
        )

        assert result['compliant'] is True
        assert 'No data available' in result['details']

    @pytest.mark.asyncio
    async def test_execute_metric_check_calls_cloudwatch_correctly(self):
        """Test that CloudWatch is called with correct parameters."""
        mock_cw = MagicMock()
        mock_cw.get_metric_statistics.return_value = {'Datapoints': []}

        clients = {'cloudwatch': mock_cw}
        check_config = {
            'metric_name': 'apiserver_storage_size_bytes',
            'namespace': 'AWS/EKS',
            'statistic': 'Maximum',
            'threshold': 6442450944,
            'threshold_description': 'test',
        }

        await self.handler._execute_metric_check(
            'OB3', check_config, clients, 'my-cluster', 7
        )

        mock_cw.get_metric_statistics.assert_called_once()
        call_kwargs = mock_cw.get_metric_statistics.call_args[1]
        assert call_kwargs['Namespace'] == 'AWS/EKS'
        assert call_kwargs['MetricName'] == 'apiserver_storage_size_bytes'
        assert call_kwargs['Statistics'] == ['Maximum']
        assert call_kwargs['Period'] == 3600
        assert call_kwargs['Dimensions'] == [
            {'Name': 'ClusterName', 'Value': 'my-cluster'}
        ]

    @pytest.mark.asyncio
    async def test_execute_metric_check_with_data(self):
        """Test metric check with actual datapoints."""
        now = datetime.now(timezone.utc)
        mock_cw = MagicMock()
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Timestamp': now - timedelta(hours=2), 'Sum': 30},
                {'Timestamp': now - timedelta(hours=1), 'Sum': 40},
                {'Timestamp': now, 'Sum': 50},
            ]
        }

        clients = {'cloudwatch': mock_cw}
        check_config = {
            'metric_name': 'apiserver_request_total_429',
            'namespace': 'AWS/EKS',
            'statistic': 'Sum',
            'threshold': 50,
            'threshold_description': 'More than 50 throttled requests',
        }

        result = await self.handler._execute_metric_check(
            'OB2', check_config, clients, 'test-cluster', 7
        )

        # Total = 30 + 40 + 50 = 120 > 50 threshold
        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_execute_metric_check_cloudwatch_error(self):
        """Test metric check when CloudWatch API throws an error."""
        mock_cw = MagicMock()
        mock_cw.get_metric_statistics.side_effect = Exception('AccessDenied')

        clients = {'cloudwatch': mock_cw}
        check_config = {
            'metric_name': 'apiserver_request_total_5XX',
            'namespace': 'AWS/EKS',
            'statistic': 'Sum',
            'threshold': 100,
            'threshold_description': 'test',
        }

        result = await self.handler._execute_metric_check(
            'OB1', check_config, clients, 'test-cluster', 7
        )

        assert result['compliant'] is False
        assert 'AccessDenied' in result['details']


class TestCheckEksObservabilityIntegration:
    """Integration tests for the full observability check flow."""

    def setup_method(self):
        self.mock_mcp = MagicMock()
        self.mock_cache = MagicMock()
        self.handler = EKSObservabilityHandler(self.mock_mcp, self.mock_cache)

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_full_check_unsupported_version(self, mock_aws_helper):
        """Test that clusters below 1.28 get an error response."""
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.return_value = {
            'cluster': {'version': '1.27'}
        }

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client
        ctx = MagicMock(spec=Context)

        result = await self.handler.check_eks_observability(
            ctx, 'test-cluster', None, None
        )

        assert isinstance(result, ObservabilityCheckResponse)
        assert result.isError is True
        assert '1.27' in result.summary
        assert '1.28' in result.summary

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_full_check_all_compliant(self, mock_aws_helper):
        """Test full check where all metrics are within thresholds."""
        now = datetime.now(timezone.utc)
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.return_value = {
            'cluster': {'version': '1.30'}
        }
        # Return low values for all metrics
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Timestamp': now - timedelta(hours=1), 'Sum': 0, 'Maximum': 0, 'Average': 0.0},
            ]
        }

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client
        ctx = MagicMock(spec=Context)

        result = await self.handler.check_eks_observability(
            ctx, 'test-cluster', None, 7
        )

        assert isinstance(result, ObservabilityCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is True
        assert len(result.check_results) == 5
        assert all(r['compliant'] for r in result.check_results)

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_full_check_with_failures(self, mock_aws_helper):
        """Test full check where etcd size exceeds threshold."""
        now = datetime.now(timezone.utc)
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.return_value = {
            'cluster': {'version': '1.30'}
        }

        def cw_side_effect(**kwargs):
            metric = kwargs.get('MetricName', '')
            if metric == 'apiserver_storage_size_bytes':
                # 7GB — exceeds 6GB threshold
                return {
                    'Datapoints': [
                        {'Timestamp': now, 'Maximum': 7_516_192_768},
                    ]
                }
            return {
                'Datapoints': [
                    {'Timestamp': now, 'Sum': 0, 'Maximum': 0, 'Average': 0.0},
                ]
            }

        mock_cw.get_metric_statistics.side_effect = cw_side_effect

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client
        ctx = MagicMock(spec=Context)

        result = await self.handler.check_eks_observability(
            ctx, 'test-cluster', None, 7
        )

        assert isinstance(result, ObservabilityCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False

        # Find the etcd check result
        etcd_result = next(
            r for r in result.check_results if r['check_name'] == 'etcd Storage Size'
        )
        assert etcd_result['compliant'] is False
        assert 'test-cluster' in etcd_result['impacted_resources']

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_full_check_connection_error(self, mock_aws_helper):
        """Test full check when AWS clients fail to initialize."""
        mock_aws_helper.create_boto3_client.side_effect = Exception('No creds')
        ctx = MagicMock(spec=Context)

        result = await self.handler.check_eks_observability(
            ctx, 'test-cluster', None, None
        )

        assert isinstance(result, ObservabilityCheckResponse)
        assert result.isError is True
        assert result.overall_compliant is False

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_observability_handler.AwsHelper')
    async def test_custom_lookback_days(self, mock_aws_helper):
        """Test that custom lookback_days is passed through."""
        now = datetime.now(timezone.utc)
        mock_eks = MagicMock()
        mock_cw = MagicMock()
        mock_eks.describe_cluster.return_value = {
            'cluster': {'version': '1.30'}
        }
        mock_cw.get_metric_statistics.return_value = {
            'Datapoints': [
                {'Timestamp': now, 'Sum': 0, 'Maximum': 0, 'Average': 0.0},
            ]
        }

        def create_client(service, region_name=None):
            if service == 'eks':
                return mock_eks
            elif service == 'cloudwatch':
                return mock_cw
            return MagicMock()

        mock_aws_helper.create_boto3_client.side_effect = create_client
        ctx = MagicMock(spec=Context)

        result = await self.handler.check_eks_observability(
            ctx, 'test-cluster', None, 14
        )

        assert result.isError is False
        # Verify the summary mentions the lookback
        content = json.loads(result.content[0].text)
        assert '14-day' in content['summary']
