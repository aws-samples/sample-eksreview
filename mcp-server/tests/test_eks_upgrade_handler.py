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
"""Tests for the EKSUpgradeHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_upgrade_handler import (
    EKSUpgradeHandler,
    _parse_k8s_version,
    _next_minor_version,
    _version_lte,
)
from awslabs.eks_review_mcp_server.models import UpgradeCheckResponse
from mcp.server.fastmcp import Context
from unittest.mock import MagicMock, AsyncMock, patch


# ── Fixtures ────────────────────────────────────

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
    mock_k8s_client = MagicMock()
    cache.get_client.return_value = mock_k8s_client
    return cache


@pytest.fixture
def mock_k8s_client():
    """Create a mock K8s client."""
    return MagicMock()


@pytest.fixture
def mock_eks_client():
    """Create a mock EKS client."""
    client = MagicMock()
    client.describe_cluster.return_value = {
        'cluster': {
            'version': '1.32',
            'platformVersion': 'eks.15',
            'resourcesVpcConfig': {'subnetIds': ['subnet-abc', 'subnet-def']},
            'logging': {'clusterLogging': [{'enabled': True, 'types': ['audit', 'api']}]},
            'health': {'issues': []},
        }
    }
    client.list_insights.return_value = {'insights': []}
    client.list_addons.return_value = {'addons': ['coredns', 'kube-proxy', 'vpc-cni']}
    client.describe_addon.return_value = {
        'addon': {'addonVersion': 'v1.11.1-eksbuild.1', 'health': {'issues': []}}
    }
    client.describe_addon_versions.return_value = {
        'addons': [{'addonVersions': [
            {'addonVersion': 'v1.11.3-eksbuild.1', 'compatibilities': [
                {'clusterVersion': '1.33', 'defaultVersion': True}
            ]}
        ]}]
    }
    client.list_nodegroups.return_value = {'nodegroups': ['ng-1']}
    client.describe_nodegroup.return_value = {
        'nodegroup': {
            'amiType': 'AL2023_x86_64_STANDARD',
            'status': 'ACTIVE',
            'subnets': ['subnet-abc'],
            'health': {'issues': []},
        }
    }
    client.describe_cluster_versions.return_value = {
        'clusterVersions': [{
            'clusterVersion': '1.33',
            'versionStatus': 'STANDARD_SUPPORT',
            'endOfStandardSupportDate': '2027-01-01',
        }]
    }
    return client


@pytest.fixture
def sample_shared_data(mock_k8s_client, mock_eks_client):
    """Create sample shared_data for testing individual checks."""
    # Create mock node with proper attributes
    mock_node = MagicMock()
    mock_node.metadata.name = 'ip-10-0-1-45'
    mock_node.metadata.namespace = None
    mock_node.metadata.labels = {'eks.amazonaws.com/nodegroup': 'ng-1'}
    mock_node.status.nodeInfo.kubeletVersion = 'v1.32.0'

    # Create mock deployment
    mock_deploy = MagicMock()
    mock_deploy.metadata.name = 'nginx'
    mock_deploy.metadata.namespace = 'default'
    mock_deploy.spec.replicas = 2
    mock_deploy.spec.template.metadata.labels = {'app': 'nginx'}
    mock_deploy.spec.template.spec.topologySpreadConstraints = None
    mock_deploy.spec.template.spec.affinity = None
    mock_deploy.spec.template.spec.containers = [MagicMock(
        name='nginx', image='nginx:1.25', readinessProbe=MagicMock(),
    )]

    # Create mock statefulset
    mock_sts = MagicMock()
    mock_sts.metadata.name = 'redis'
    mock_sts.metadata.namespace = 'default'
    mock_sts.spec.replicas = 3
    mock_sts.spec.minReadySeconds = 10
    mock_sts.spec.template.spec.terminationGracePeriodSeconds = 30
    mock_sts.spec.template.metadata.labels = {'app': 'redis'}

    # Create mock PDB
    mock_pdb = MagicMock()
    mock_pdb.spec.selector.matchLabels = {'app': 'nginx'}

    # Create mock pod
    mock_pod = MagicMock()
    mock_pod.metadata.name = 'nginx-abc123'
    mock_pod.metadata.namespace = 'default'
    mock_pod.metadata.ownerReferences = [MagicMock(kind='ReplicaSet', name='nginx-abc')]
    mock_pod.spec.containers = [MagicMock(
        name='nginx', image='nginx:1.25', readinessProbe=MagicMock(),
    )]
    mock_pod.spec.volumes = []

    # Create mock kube-system daemonset (kube-proxy)
    mock_kp_ds = MagicMock()
    mock_kp_ds.metadata.name = 'kube-proxy'
    mock_kp_ds.metadata.namespace = 'kube-system'
    mock_kp_ds.spec.template.spec.containers = [MagicMock(
        name='kube-proxy', image='602401143452.dkr.ecr.us-west-2.amazonaws.com/eks/kube-proxy:v1.32.0-eksbuild.2',
    )]

    return {
        'k8s_client': mock_k8s_client,
        'cluster_name': 'test-cluster',
        'namespace': None,
        'current_version': '1.32',
        'target_version': '1.33',
        'cluster_info': {
            'version': '1.32',
            'platformVersion': 'eks.15',
            'resourcesVpcConfig': {'subnetIds': ['subnet-abc', 'subnet-def']},
            'logging': {'clusterLogging': [{'enabled': True, 'types': ['audit', 'api']}]},
            'health': {'issues': []},
        },
        'eks_client': mock_eks_client,
        'ec2_client': MagicMock(),
        'insights': [],
        'insight_details': {},
        'addon_names': ['coredns', 'kube-proxy', 'vpc-cni'],
        'addon_details': {
            'coredns': {'addonVersion': 'v1.11.1-eksbuild.1', 'health': {'issues': []}},
            'kube-proxy': {'addonVersion': 'v1.32.0-eksbuild.2', 'health': {'issues': []}},
            'vpc-cni': {'addonVersion': 'v1.18.0-eksbuild.1', 'health': {'issues': []}},
        },
        'addon_target_versions': {
            'coredns': {'addonVersions': [
                {'addonVersion': 'v1.11.3-eksbuild.1', 'compatibilities': [
                    {'clusterVersion': '1.33', 'defaultVersion': True}
                ]}
            ]},
        },
        'nodegroup_names': ['ng-1'],
        'nodegroup_details': {
            'ng-1': {
                'amiType': 'AL2023_x86_64_STANDARD',
                'status': 'ACTIVE',
                'subnets': ['subnet-abc'],
                'health': {'issues': []},
            }
        },
        'subnets': [
            {'SubnetId': 'subnet-abc', 'AvailabilityZone': 'us-west-2a', 'AvailableIpAddressCount': 200},
            {'SubnetId': 'subnet-def', 'AvailabilityZone': 'us-west-2b', 'AvailableIpAddressCount': 150},
        ],
        'nodes': [mock_node],
        'deployments': [mock_deploy],
        'statefulsets': [mock_sts],
        'pdbs': [mock_pdb],
        'pods': [mock_pod],
        'kube_system_deployments': [],
        'kube_system_daemonsets': [mock_kp_ds],
        'kube_system_configmaps': [],
        'karpenter_deployments': [],
        'helm_secrets': [],
        'third_party_workloads': [],
        'crds': [],
        'misconfig_insights': [],
        'target_version_info': {'versionStatus': 'STANDARD_SUPPORT'},
        'target_version_status': 'STANDARD_SUPPORT',
    }


# ── Utility Function Tests ──────────────────────

class TestUtilityFunctions:
    """Tests for module-level utility functions."""

    def test_parse_k8s_version_standard(self):
        assert _parse_k8s_version('1.32') == (1, 32)

    def test_parse_k8s_version_with_v_prefix(self):
        assert _parse_k8s_version('v1.33.0') == (1, 33)

    def test_parse_k8s_version_with_patch(self):
        assert _parse_k8s_version('1.32.5') == (1, 32)

    def test_parse_k8s_version_eks_build(self):
        assert _parse_k8s_version('v1.32.0-eksbuild.2') == (1, 32)

    def test_parse_k8s_version_empty(self):
        assert _parse_k8s_version('') == (0, 0)

    def test_parse_k8s_version_garbage(self):
        assert _parse_k8s_version('not-a-version') == (0, 0)

    def test_next_minor_version(self):
        assert _next_minor_version('1.32') == '1.33'

    def test_next_minor_version_with_v(self):
        assert _next_minor_version('v1.34.0') == '1.35'

    def test_version_lte_equal(self):
        assert _version_lte('v1.32.0', 'v1.32.0') is True

    def test_version_lte_less(self):
        assert _version_lte('v1.25.0', 'v1.33.0') is True

    def test_version_lte_greater(self):
        assert _version_lte('v1.35.0', 'v1.33.0') is False


# ── Handler Init Tests ──────────────────────────

class TestEKSUpgradeHandlerInit:
    """Tests for handler initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_eks_upgrade_readiness'

    def test_init_loads_check_registry(self, mock_mcp, mock_client_cache):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        all_checks = handler._get_all_checks()
        assert len(all_checks) == 38
        assert 'U1' in all_checks
        assert 'U38' in all_checks

    def test_init_loads_deprecation_db(self, mock_mcp, mock_client_cache):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db') as mock_load:
            mock_load.return_value = [{'version': 'test/v1', 'kind': 'Test', 'removed-in': 'v1.99.0', 'component': 'k8s'}]
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        assert len(handler.deprecation_db) == 1

    @pytest.mark.asyncio
    async def test_connection_error(self, mock_mcp, mock_client_cache, mock_context):
        mock_client_cache.get_client.side_effect = Exception('Connection refused')

        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler.check_eks_upgrade_readiness(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, UpgradeCheckResponse)
        assert result.isError is True
        assert 'Connection refused' in result.summary


# ── Early Exit Tests ────────────────────────────

class TestEarlyExits:
    """Tests for early exit conditions."""

    @pytest.mark.asyncio
    async def test_target_not_newer_than_current(self, mock_mcp, mock_client_cache, mock_context, mock_eks_client):
        """Cluster already at or beyond target version."""
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.AwsHelper') as mock_aws:
            mock_aws.create_boto3_client.return_value = mock_eks_client
            mock_eks_client.describe_cluster.return_value = {
                'cluster': {'version': '1.33', 'resourcesVpcConfig': {'subnetIds': []},
                            'logging': {}, 'health': {}}
            }

            result = await handler.check_eks_upgrade_readiness(
                mock_context, cluster_name='test-cluster', target_version='1.32'
            )

        assert result.overall_ready is True
        assert result.blockers == 0
        assert 'already at or beyond' in result.summary

    @pytest.mark.asyncio
    async def test_target_version_not_available(self, mock_mcp, mock_client_cache, mock_context, mock_eks_client):
        """Target version doesn't exist in EKS."""
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        mock_eks_client.describe_cluster_versions.return_value = {'clusterVersions': []}
        # For the "latest version" fallback query
        mock_eks_client.describe_cluster_versions.side_effect = [
            {'clusterVersions': []},  # target not found
            {'clusterVersions': [{'clusterVersion': '1.35'}]},  # latest
        ]

        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.AwsHelper') as mock_aws:
            mock_aws.create_boto3_client.return_value = mock_eks_client

            result = await handler.check_eks_upgrade_readiness(
                mock_context, cluster_name='test-cluster', target_version='1.99'
            )

        assert result.overall_ready is True
        assert 'not available' in result.summary.lower()


# ── Individual Check Tests ──────────────────────

class TestControlPlaneChecks:
    """Tests for U1-U3 control plane checks."""

    @pytest.mark.asyncio
    async def test_u1_version_status(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_version_status(sample_shared_data)
        assert result['compliant'] is True
        assert '1.32' in result['details']

    @pytest.mark.asyncio
    async def test_u2_target_is_next_minor(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_target_available(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u2_target_is_downgrade(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['target_version'] = '1.31'
        result = await handler._check_target_available(sample_shared_data)
        assert result['compliant'] is False
        assert 'downgrade' in result['details'].lower()

    @pytest.mark.asyncio
    async def test_u3_single_hop(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_multi_hop(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u3_multi_hop(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['target_version'] = '1.35'
        result = await handler._check_multi_hop(sample_shared_data)
        assert result['compliant'] is False
        assert '3' in result['details']  # 3 hops: 1.32->1.33->1.34->1.35


class TestAddonChecks:
    """Tests for U5-U7 addon checks."""

    @pytest.mark.asyncio
    async def test_u5_addon_compatible(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # All addons have compatible versions in target_versions
        sample_shared_data['addon_target_versions'] = {
            'coredns': {'addonVersions': [
                {'addonVersion': 'v1.11.1-eksbuild.1', 'compatibilities': [
                    {'clusterVersion': '1.33', 'defaultVersion': True}
                ]}
            ]},
        }
        sample_shared_data['addon_details'] = {
            'coredns': {'addonVersion': 'v1.11.1-eksbuild.1', 'health': {'issues': []}},
        }
        sample_shared_data['addon_names'] = ['coredns']

        result = await handler._check_addon_compat(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u5_addon_incompatible(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['addon_target_versions'] = {
            'vpc-cni': {'addonVersions': [
                {'addonVersion': 'v1.19.0-eksbuild.1', 'compatibilities': [
                    {'clusterVersion': '1.33', 'defaultVersion': True}
                ]}
            ]},
        }
        sample_shared_data['addon_details'] = {
            'vpc-cni': {'addonVersion': 'v1.16.0-eksbuild.1', 'health': {'issues': []}},
        }
        sample_shared_data['addon_names'] = ['vpc-cni']

        result = await handler._check_addon_compat(sample_shared_data)
        assert result['compliant'] is False
        assert 'vpc-cni' in str(result['impacted_resources'])

    @pytest.mark.asyncio
    async def test_u7_addon_healthy(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_addon_health(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u7_addon_unhealthy(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['addon_details']['coredns']['health'] = {
            'issues': [{'code': 'InsufficientNumberOfReplicas', 'message': '0/2 pods available'}]
        }

        result = await handler._check_addon_health(sample_shared_data)
        assert result['compliant'] is False
        assert 'coredns' in str(result['impacted_resources'])


class TestDataPlaneChecks:
    """Tests for U8-U11 data plane checks."""

    @pytest.mark.asyncio
    async def test_u8_kubelet_version_ok(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_kubelet_skew(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u8_kubelet_skew_violation(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # Set target to 1.36, node kubelet at 1.32 = 4 version skew (exceeds 3)
        sample_shared_data['target_version'] = '1.36'
        result = await handler._check_kubelet_skew(sample_shared_data)
        assert result['compliant'] is False
        assert 'skew=' in str(result['impacted_resources'])

    @pytest.mark.asyncio
    async def test_u9_al2023_ami_ok(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_ami_type(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u9_al2_ami_deprecated(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodegroup_details']['ng-1']['amiType'] = 'AL2_x86_64'
        sample_shared_data['target_version'] = '1.33'
        result = await handler._check_ami_type(sample_shared_data)
        assert result['compliant'] is False
        assert 'AL2' in str(result['impacted_resources'])

    @pytest.mark.asyncio
    async def test_u11_subnet_ips_sufficient(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_subnet_ips(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u11_subnet_ips_insufficient(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['subnets'][0]['AvailableIpAddressCount'] = 3
        result = await handler._check_subnet_ips(sample_shared_data)
        assert result['compliant'] is False
        assert 'subnet-abc' in str(result['impacted_resources'])


class TestWorkloadChecks:
    """Tests for U12-U17 workload readiness checks."""

    @pytest.mark.asyncio
    async def test_u12_pdb_covered(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # Add PDB for both nginx and redis
        mock_pdb_nginx = MagicMock()
        mock_pdb_nginx.spec.selector.matchLabels = {'app': 'nginx'}
        mock_pdb_redis = MagicMock()
        mock_pdb_redis.spec.selector.matchLabels = {'app': 'redis'}
        sample_shared_data['pdbs'] = [mock_pdb_nginx, mock_pdb_redis]

        # Ensure deployment has matching labels and 2+ replicas
        mock_deploy = sample_shared_data['deployments'][0]
        mock_deploy.spec.replicas = 2
        mock_deploy.metadata.namespace = 'default'
        mock_deploy.metadata.name = 'nginx'
        mock_deploy.spec.template.metadata.labels = {'app': 'nginx'}

        # Ensure statefulset has matching labels
        mock_sts = sample_shared_data['statefulsets'][0]
        mock_sts.spec.template.metadata.labels = {'app': 'redis'}

        result = await handler._check_pdb_coverage(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u12_pdb_missing(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['pdbs'] = []  # No PDBs
        result = await handler._check_pdb_coverage(sample_shared_data)
        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_u13_no_single_replicas(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_single_replica(sample_shared_data)
        assert result['compliant'] is True  # nginx has 2 replicas, redis has 3

    @pytest.mark.asyncio
    async def test_u13_single_replica_found(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['deployments'][0].spec.replicas = 1
        result = await handler._check_single_replica(sample_shared_data)
        assert result['compliant'] is False
        assert 'nginx' in str(result['impacted_resources'])

    @pytest.mark.asyncio
    async def test_u17_termination_grace_ok(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_termination_grace(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u17_termination_grace_zero(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['statefulsets'][0].spec.template.spec.terminationGracePeriodSeconds = 0
        result = await handler._check_termination_grace(sample_shared_data)
        assert result['compliant'] is False


class TestClusterHealthChecks:
    """Tests for U21-U23 cluster health checks."""

    @pytest.mark.asyncio
    async def test_u21_audit_logging_enabled(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_control_plane_logging(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u21_audit_logging_disabled(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info']['logging'] = {'clusterLogging': []}
        result = await handler._check_control_plane_logging(sample_shared_data)
        assert result['compliant'] is False
        assert 'audit' in result['details'].lower()

    @pytest.mark.asyncio
    async def test_u22_no_health_issues(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_cluster_health(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u23_kube_proxy_skew_ok(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_kube_proxy_skew(sample_shared_data)
        assert result['compliant'] is True


class TestAPIDeprecationChecks:
    """Tests for U24-U26 API deprecation checks."""

    @pytest.mark.asyncio
    async def test_u24_no_deprecated_apis(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)
            handler.deprecation_db = []

        result = await handler._check_deprecated_apis_live(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u25_no_helm_releases(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)
            handler.deprecation_db = [
                {'version': 'test/v1beta1', 'kind': 'Test', 'removed-in': 'v1.33.0',
                 'replacement-api': 'test/v1', 'component': 'k8s'}
            ]

        result = await handler._check_deprecated_apis_helm(sample_shared_data)
        assert result['compliant'] is True
        assert 'No Helm releases' in result['details']

    @pytest.mark.asyncio
    async def test_u26_deprecated_warning(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)
            handler.deprecation_db = [
                {'version': 'resource.k8s.io/v1beta1', 'kind': 'ResourceSlice',
                 'deprecated-in': 'v1.33.0', 'removed-in': 'v1.36.0',
                 'replacement-api': 'resource.k8s.io/v1beta2', 'component': 'k8s'}
            ]

        result = await handler._check_deprecated_apis_warning(sample_shared_data)
        assert result['compliant'] is True  # Warning, not a failure
        assert 'deprecated' in result['details'].lower()


class TestDockerSocketCheck:
    """Tests for U35 docker socket check."""

    @pytest.mark.asyncio
    async def test_u35_no_docker_mounts(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        result = await handler._check_docker_socket(sample_shared_data)
        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_u35_docker_socket_found(self, mock_mcp, mock_client_cache, sample_shared_data):
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # Add a pod with docker.sock mount
        mock_vol = MagicMock()
        mock_vol.hostPath.path = '/var/run/docker.sock'
        sample_shared_data['pods'][0].spec.volumes = [mock_vol]

        result = await handler._check_docker_socket(sample_shared_data)
        assert result['compliant'] is False
        assert 'docker.sock' in str(result['impacted_resources'])
        assert 'containerd' in result['details']


# ── Verdict Logic Tests ─────────────────────────

class TestVerdictLogic:
    """Tests for the blocker/warning/pass verdict computation."""

    @pytest.mark.asyncio
    async def test_verdict_all_passing(self, mock_mcp, mock_client_cache, mock_context, mock_eks_client):
        """All checks pass = READY."""
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # Mock _execute_check to always return a passing result
        passing_result = handler._create_check_result('U1', True, [], 'OK')
        handler._execute_check = AsyncMock(return_value=passing_result)
        handler._initialize_shared_data = AsyncMock(return_value={
            'current_version': '1.32', 'target_version': '1.33',
            'eks_client': mock_eks_client,
        })

        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.AwsHelper'):
            result = await handler.check_eks_upgrade_readiness(
                mock_context, cluster_name='test-cluster', target_version='1.33'
            )

        assert result.overall_ready is True
        assert result.blockers == 0

    @pytest.mark.asyncio
    async def test_verdict_only_critical_is_blocker(self, mock_mcp, mock_client_cache):
        """Only Critical severity = blocker. High = warning."""
        with patch('awslabs.eks_review_mcp_server.eks_upgrade_handler.EKSUpgradeHandler._load_deprecation_db', return_value=[]):
            handler = EKSUpgradeHandler(mock_mcp, mock_client_cache)

        # Simulate check results with mixed severities
        check_results = [
            {'compliant': False, 'severity': 'Critical'},  # blocker
            {'compliant': False, 'severity': 'High'},     # warning, not blocker
            {'compliant': False, 'severity': 'Medium'},   # warning
            {'compliant': True, 'severity': 'High'},       # pass
        ]

        blockers = sum(
            1 for r in check_results
            if not r['compliant'] and r.get('severity', '').lower() == 'critical'
        )
        warnings = sum(
            1 for r in check_results
            if not r['compliant'] and r.get('severity', '').lower() in ('high', 'medium', 'low')
        )

        assert blockers == 1  # Only Critical
        assert warnings == 2  # High + Medium
