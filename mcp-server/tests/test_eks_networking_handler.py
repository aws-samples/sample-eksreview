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
"""Tests for the EKSNetworkingHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_networking_handler import EKSNetworkingHandler
from awslabs.eks_review_mcp_server.models import NetworkingCheckResponse
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
    mock_k8s_client = MagicMock()
    cache.get_client.return_value = mock_k8s_client
    return cache


@pytest.fixture
def mock_eks_client():
    """Create a mock EKS client."""
    return MagicMock()


@pytest.fixture
def mock_k8s_client():
    """Create a mock K8s client."""
    return MagicMock()


@pytest.fixture
def mock_ec2_client():
    """Create a mock EC2 client."""
    return MagicMock()


class TestEKSNetworkingHandlerInit:
    """Tests for the EKSNetworkingHandler class initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization of EKSNetworkingHandler."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_eks_networking'

    @pytest.mark.asyncio
    async def test_check_eks_networking_connection_error(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_eks_networking with a connection error."""
        mock_client_cache.get_client.side_effect = Exception('Failed to connect to cluster')

        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock _initialize_clients to return None
        handler._initialize_clients = AsyncMock(return_value=None)

        result = await handler.check_eks_networking(
            mock_context, cluster_name='test-cluster', region='us-west-2'
        )

        assert isinstance(result, NetworkingCheckResponse)
        assert result.isError is True
        assert 'Failed to initialize required clients' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] == 'Connection Error'
        assert result.check_results[0]['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_eks_networking_invalid_region(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_eks_networking with invalid region."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        handler._initialize_clients = AsyncMock(return_value=None)

        result = await handler.check_eks_networking(
            mock_context, cluster_name='test-cluster', region='invalid-region'
        )

        assert isinstance(result, NetworkingCheckResponse)
        assert result.isError is True
        assert result.overall_compliant is False

    @pytest.mark.asyncio
    async def test_check_eks_networking_cluster_not_found(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Networking check fails fast when the cluster cannot be described.

        Client init can succeed (creating boto3 clients makes no AWS call),
        but if describe_cluster fails (cluster missing / no access),
        _get_cluster_info returns None. The handler must return an error
        response rather than running checks against empty data and
        reporting a misleading pass.
        """
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Init succeeds (boto3 clients created), but cluster lookup fails.
        handler._initialize_clients = AsyncMock(
            return_value={'eks': MagicMock(), 'ec2': MagicMock(), 'k8s': None}
        )
        handler._get_cluster_info = AsyncMock(return_value=None)

        result = await handler.check_eks_networking(
            mock_context, cluster_name='missing-cluster', region='us-west-2'
        )

        assert isinstance(result, NetworkingCheckResponse)
        assert result.isError is True
        assert result.overall_compliant is False
        assert 'missing-cluster' in result.summary


class TestEKSNetworkingHandlerChecks:
    """Tests for the EKSNetworkingHandler check methods."""

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_compliant_private_only(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with private-only endpoint access."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': False,
            'public_access_cidrs': [],
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0
        assert isinstance(result['details'], str)
        assert 'private only' in result['details']
        assert 'check_id' not in result
        assert 'remediation' not in result

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_compliant_restricted_public(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with restricted public access."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['10.0.0.0/8', '192.168.1.0/24'],
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0
        assert isinstance(result['details'], str)
        assert 'restricted public access' in result['details']

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_non_compliant_unrestricted(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with unrestricted public access."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': False,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['0.0.0.0/0'],
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is False
        assert 'Cluster: test-cluster' in result['impacted_resources']
        assert isinstance(result['details'], str)
        assert 'unrestricted public access' in result['details']
        assert '0.0.0.0/0' in result['details']

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_fallback_to_api(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check falls back to API call when cluster_info not provided."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_eks_client.describe_cluster.return_value = {
            'cluster': {
                'resourcesVpcConfig': {
                    'endpointConfigPrivateAccess': True,
                    'endpointConfigPublicAccess': False,
                    'publicAccessCidrs': []
                }
            }
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info=None
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        mock_eks_client.describe_cluster.assert_called_once_with(name='test-cluster')

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_public_only_restricted(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with public-only endpoint but restricted CIDRs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': False,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['10.0.0.0/8'],  # Restricted CIDR
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        # With restricted CIDRs, this is considered compliant even without private access
        assert result['compliant'] is True
        assert isinstance(result['details'], str)

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_public_only_unrestricted(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with public-only endpoint and unrestricted access."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': False,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['0.0.0.0/0'],  # Unrestricted
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is False
        assert 'Cluster: test-cluster' in result['impacted_resources']
        assert isinstance(result['details'], str)

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_missing_config(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with missing endpoint configuration."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {}  # Missing endpoint config

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        # Should handle gracefully
        assert 'check_name' in result
        assert isinstance(result['details'], str)

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_multiple_restricted_cidrs(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with multiple restricted CIDRs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': True,
            'public_access_cidrs': [
                '10.0.0.0/8',
                '172.16.0.0/12',
                '192.168.0.0/16'
            ],
            'is_auto_mode': False
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0
        assert isinstance(result['details'], str)


    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_compliant(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with nodes distributed across multiple AZs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock nodes in 3 AZs with even distribution
        mock_node1 = MagicMock()
        mock_node1.to_dict.return_value = {
            'metadata': {
                'name': 'node-1',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2a'}
            }
        }
        mock_node2 = MagicMock()
        mock_node2.to_dict.return_value = {
            'metadata': {
                'name': 'node-2',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2b'}
            }
        }
        mock_node3 = MagicMock()
        mock_node3.to_dict.return_value = {
            'metadata': {
                'name': 'node-3',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2c'}
            }
        }

        mock_response = MagicMock()
        mock_response.items = [mock_node1, mock_node2, mock_node3]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        assert '3 AZs' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_non_compliant_single_az(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with nodes in single AZ."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock nodes all in one AZ
        mock_node1 = MagicMock()
        mock_node1.to_dict.return_value = {
            'metadata': {
                'name': 'node-1',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2a'}
            }
        }
        mock_node2 = MagicMock()
        mock_node2.to_dict.return_value = {
            'metadata': {
                'name': 'node-2',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2a'}
            }
        }

        mock_response = MagicMock()
        mock_response.items = [mock_node1, mock_node2]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'single AZ' in result['details']
        assert 'All nodes in single AZ' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_non_compliant_uneven(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with uneven node distribution across AZs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock nodes with uneven distribution (5 in one AZ, 1 in another)
        nodes = []
        for i in range(5):
            node = MagicMock()
            node.to_dict.return_value = {
                'metadata': {
                    'name': f'node-{i}',
                    'labels': {'topology.kubernetes.io/zone': 'us-west-2a'}
                }
            }
            nodes.append(node)
        
        node = MagicMock()
        node.to_dict.return_value = {
            'metadata': {
                'name': 'node-5',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2b'}
            }
        }
        nodes.append(node)

        mock_response = MagicMock()
        mock_response.items = nodes
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'Uneven' in result['details']
        assert '2 AZs' in result['details']
        assert len(result['impacted_resources']) > 0

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_no_nodes(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with no nodes found."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'No nodes found' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_missing_az_labels(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with nodes missing AZ labels."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock node without AZ label
        mock_node = MagicMock()
        mock_node.to_dict.return_value = {
            'metadata': {
                'name': 'node-1',
                'labels': {}  # No AZ label
            }
        }

        mock_response = MagicMock()
        mock_response.items = [mock_node]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is False
        assert 'missing AZ label' in result['impacted_resources'][0]
        assert isinstance(result['details'], str)

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_auto_mode(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check with EKS Auto Mode cluster."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        cluster_info = {
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['10.0.0.0/8'],
            'is_auto_mode': True,
            'auto_mode_features': {
                'compute_enabled': True,
                'storage_enabled': True
            }
        }

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        assert 'restricted public access' in result['details']

    @pytest.mark.asyncio
    async def test_check_cluster_endpoint_access_error_handling(
        self, mock_mcp, mock_client_cache, mock_eks_client
    ):
        """Test N1 check error handling."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_eks_client.describe_cluster.side_effect = Exception('API Error')

        result = await handler._check_cluster_endpoint_access(
            'test-cluster', 'us-west-2', mock_eks_client, cluster_info=None
        )

        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'Failed to check cluster endpoint access' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_legacy_label(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with legacy AZ label format."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock node with legacy label
        mock_node = MagicMock()
        mock_node.to_dict.return_value = {
            'metadata': {
                'name': 'node-1',
                'labels': {'failure-domain.beta.kubernetes.io/zone': 'us-west-2a'}
            }
        }

        mock_response = MagicMock()
        mock_response.items = [mock_node]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        # Should be non-compliant due to single AZ, but should recognize the legacy label
        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'single AZ' in result['details']
        assert 'us-west-2a' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_mixed_labels(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with mixed modern and legacy labels."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock node with modern label
        mock_node1 = MagicMock()
        mock_node1.to_dict.return_value = {
            'metadata': {
                'name': 'node-1',
                'labels': {'topology.kubernetes.io/zone': 'us-west-2a'}
            }
        }

        # Mock node with legacy label
        mock_node2 = MagicMock()
        mock_node2.to_dict.return_value = {
            'metadata': {
                'name': 'node-2',
                'labels': {'failure-domain.beta.kubernetes.io/zone': 'us-west-2b'}
            }
        }

        mock_response = MagicMock()
        mock_response.items = [mock_node1, mock_node2]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        assert '2 AZs' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_api_error(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with API error."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_k8s_client.list_resources.side_effect = Exception('API error')

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is False
        assert isinstance(result['details'], str)
        assert 'Failed to check' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_four_azs(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with nodes distributed across 4 AZs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock nodes in 4 AZs
        nodes = []
        for i, az in enumerate(['us-west-2a', 'us-west-2b', 'us-west-2c', 'us-west-2d']):
            node = MagicMock()
            node.to_dict.return_value = {
                'metadata': {
                    'name': f'node-{i}',
                    'labels': {'topology.kubernetes.io/zone': az}
                }
            }
            nodes.append(node)

        mock_response = MagicMock()
        mock_response.items = nodes
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        assert '4 AZs' in result['details']

    @pytest.mark.asyncio
    async def test_check_multi_az_node_distribution_two_azs_balanced(
        self, mock_mcp, mock_client_cache, mock_k8s_client
    ):
        """Test N2 check with nodes balanced across 2 AZs."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        # Mock nodes evenly distributed across 2 AZs
        nodes = []
        for i in range(4):
            node = MagicMock()
            az = 'us-west-2a' if i % 2 == 0 else 'us-west-2b'
            node.to_dict.return_value = {
                'metadata': {
                    'name': f'node-{i}',
                    'labels': {'topology.kubernetes.io/zone': az}
                }
            }
            nodes.append(node)

        mock_response = MagicMock()
        mock_response.items = nodes
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_multi_az_node_distribution(
            'test-cluster', 'us-west-2', mock_k8s_client
        )

        assert result['compliant'] is True
        assert isinstance(result['details'], str)
        assert '2 AZs' in result['details']



class TestEKSNetworkingHandlerIntegration:
    """Integration-style tests for complete networking check flow."""

    @pytest.mark.asyncio
    async def test_full_networking_check_all_compliant(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full networking check with all checks passing."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        mock_k8s_client = MagicMock()

        # Mock compliant cluster endpoint
        cluster_info = {
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': False,
            'public_access_cidrs': [],
            'is_auto_mode': False
        }

        # Mock nodes distributed across 3 AZs
        nodes = []
        for i, az in enumerate(['us-west-2a', 'us-west-2b', 'us-west-2c']):
            node = MagicMock()
            node.to_dict.return_value = {
                'metadata': {
                    'name': f'node-{i}',
                    'labels': {'topology.kubernetes.io/zone': az}
                }
            }
            nodes.append(node)

        mock_response = MagicMock()
        mock_response.items = nodes
        mock_k8s_client.list_resources.return_value = mock_response

        clients = {
            'eks': mock_eks_client,
            'k8s': mock_k8s_client
        }

        handler._initialize_clients = AsyncMock(return_value=clients)

        # Mock _execute_check to return proper dicts for all checks
        async def mock_execute(check_id, *args, **kwargs):
            return {
                'check_name': f'Check {check_id}',
                'compliant': True,
                'impacted_resources': [],
                'details': 'Compliant',
            }

        # Mock _get_cluster_info to return a proper dict (not MagicMock)
        handler._get_cluster_info = AsyncMock(return_value={
            'vpc_id': 'vpc-123',
            'subnet_ids': ['subnet-abc'],
            'endpoint_config_private_access': True,
            'endpoint_config_public_access': False,
            'public_access_cidrs': [],
            'is_auto_mode': False,
        })

        with patch.object(handler, '_execute_check', side_effect=mock_execute):
            result = await handler.check_eks_networking(
                mock_context, cluster_name='test-cluster', region='us-west-2'
            )

        assert isinstance(result, NetworkingCheckResponse)
        assert result.isError is False
        # With all compliant checks, overall should be compliant
        passed_checks = sum(1 for check in result.check_results if check['compliant'])
        assert passed_checks > 0

    @pytest.mark.asyncio
    async def test_full_networking_check_with_failures(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full networking check with some checks failing."""
        handler = EKSNetworkingHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        mock_k8s_client = MagicMock()

        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        clients = {
            'eks': mock_eks_client,
            'k8s': mock_k8s_client
        }

        handler._initialize_clients = AsyncMock(return_value=clients)

        # Mock _get_cluster_info to return a proper dict
        handler._get_cluster_info = AsyncMock(return_value={
            'vpc_id': 'vpc-123',
            'subnet_ids': ['subnet-abc'],
            'endpoint_config_private_access': False,
            'endpoint_config_public_access': True,
            'public_access_cidrs': ['0.0.0.0/0'],
            'is_auto_mode': False,
        })

        # Mock _execute_check to return mixed results
        async def mock_execute(check_id, *args, **kwargs):
            if check_id in ('N1', 'N2'):
                return {
                    'check_name': f'Check {check_id}',
                    'severity': 'high',
                    'compliant': False,
                    'impacted_resources': [f'Resource for {check_id}'],
                    'details': f'{check_id} non-compliant',
                }
            return {
                'check_name': f'Check {check_id}',
                'severity': 'medium',
                'compliant': True,
                'impacted_resources': [],
                'details': 'Compliant',
            }

        with patch.object(handler, '_execute_check', side_effect=mock_execute):
            result = await handler.check_eks_networking(
                mock_context, cluster_name='test-cluster', region='us-west-2'
            )

        assert isinstance(result, NetworkingCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False
        # Should have multiple failed checks
        failed_checks = sum(1 for check in result.check_results if not check['compliant'])
        assert failed_checks > 0
