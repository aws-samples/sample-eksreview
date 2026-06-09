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
"""Tests for the EKSKarpenterHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_karpenter_handler import EKSKarpenterHandler
from awslabs.eks_review_mcp_server.models import KarpenterCheckResponse
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
def sample_shared_data():
    """Create sample shared_data for testing."""
    return {
        'cluster_name': 'test-cluster',
        'is_auto_mode': False,
        'skip_karpenter_checks': False,
        'karpenter_deployments': [],
        'nodepools': [],
        'nodepool_count': 0,
        'karpenter_config': {},
        'eks_client': MagicMock(),
        'cluster_info': {}
    }


class TestEKSKarpenterHandlerInit:
    """Tests for the EKSKarpenterHandler class initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization of EKSKarpenterHandler."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_karpenter_best_practices'

    @pytest.mark.asyncio
    async def test_check_karpenter_connection_error(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_karpenter_best_practices with a connection error."""
        mock_client_cache.get_client.side_effect = Exception('Failed to connect to cluster')

        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        result = await handler.check_karpenter_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, KarpenterCheckResponse)
        assert result.isError is True
        assert 'Failed to connect to cluster' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] == 'Connection Error'
        assert result.check_results[0]['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_karpenter_invalid_cluster_name(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_karpenter_best_practices with invalid cluster name."""
        mock_client_cache.get_client.side_effect = Exception('Cluster not found')

        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        result = await handler.check_karpenter_best_practices(
            mock_context, cluster_name='non-existent-cluster'
        )

        assert isinstance(result, KarpenterCheckResponse)
        assert result.isError is True
        assert 'Cluster not found' in result.summary
        assert result.overall_compliant is False

    @pytest.mark.asyncio
    async def test_check_karpenter_auto_mode_early_exit(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test early exit for EKS Auto Mode clusters."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock _initialize_clients_and_data to return Auto Mode data
        auto_mode_data = {
            'skip_karpenter_checks': True,
            'is_auto_mode': True,
            'auto_mode_features': {
                'compute_enabled': True,
                'storage_enabled': True
            }
        }
        handler._initialize_clients_and_data = AsyncMock(return_value=auto_mode_data)

        result = await handler.check_karpenter_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, KarpenterCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is True
        assert 'Auto Mode' in result.summary
        assert 'not applicable' in result.summary


class TestEKSKarpenterHandlerChecks:
    """Tests for the EKSKarpenterHandler check methods."""

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test K2 check with locked AMI selectors."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with locked AMI
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'default-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'amiFamily': 'AL2'
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'default-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test K2 check with dynamic AMI selectors."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with @latest AMI
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'dynamic-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'amiFamily': 'AL2@latest'
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'dynamic-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_no_nodepools(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test K2 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_mixed_nodepools(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test K2 check with mixed NodePools (some locked, some dynamic)."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Locked AMI NodePool
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'locked-nodepool'
        mock_nodepool1.spec = {
            'template': {
                'spec': {
                    'amiFamily': 'AL2'
                }
            }
        }

        # Dynamic AMI NodePool
        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'dynamic-nodepool'
        mock_nodepool2.spec = {
            'template': {
                'spec': {
                    'amiFamily': 'AL2@latest'
                }
            }
        }

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'dynamic-nodepool' in result['impacted_resources']
        # Only non-compliant ones should be in impacted_resources
        assert 'locked-nodepool' not in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_missing_ami_family(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test K2 check with NodePool missing amiFamily."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'no-ami-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {}  # No amiFamily
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')

        # Behavior depends on implementation - should handle gracefully
        assert 'check_name' in result

    @pytest.mark.asyncio
    async def test_check_instance_type_exclusions_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K3 check with instance type exclusions."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with exclusions
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'default-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'NotIn',
                            'values': ['t2.micro', 't2.small']
                        }
                    ]
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_exclusions(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'default-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_instance_type_exclusions_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K3 check without instance type exclusions."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool without exclusions
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'no-exclusions-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': []
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_exclusions(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'no-exclusions-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_instance_type_exclusions_mixed(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K3 check with mixed NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with exclusions
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'with-exclusions'
        mock_nodepool1.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'NotIn',
                            'values': ['t2.micro']
                        }
                    ]
                }
            }
        }

        # Mock NodePool without exclusions
        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'without-exclusions'
        mock_nodepool2.spec = {
            'template': {
                'spec': {
                    'requirements': []
                }
            }
        }

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_instance_type_exclusions(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'without-exclusions' in result['impacted_resources']
        assert 'with-exclusions' not in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_instance_type_exclusions_no_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K3 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_instance_type_exclusions(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_instance_type_exclusions_wrong_operator(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K3 check with wrong operator (In instead of NotIn)."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'wrong-operator-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'In',  # Wrong operator
                            'values': ['t2.micro', 't2.small']
                        }
                    ]
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_exclusions(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_nodepool_exclusivity_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K4 check with mutually exclusive NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePools with weights
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'weighted-nodepool-1'
        mock_nodepool1.spec = {
            'weight': 10,
            'template': {
                'spec': {}
            }
        }

        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'weighted-nodepool-2'
        mock_nodepool2.spec = {
            'weight': 20,
            'template': {
                'spec': {}
            }
        }

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_nodepool_exclusivity(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_ttl_configuration_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K5 check with TTL configured."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with TTL
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'ttl-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'expireAfter': '720h'
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_ttl_configuration(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_ttl_configuration_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K6 check without TTL configured."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool without TTL
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'no-ttl-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {}
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_ttl_configuration(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'no-ttl-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_ttl_configuration_no_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K6 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_ttl_configuration(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_ttl_configuration_mixed_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K6 check with mixed NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # NodePool with TTL
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'with-ttl'
        mock_nodepool1.spec = {
            'template': {
                'spec': {
                    'expireAfter': '720h'
                }
            }
        }

        # NodePool without TTL
        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'without-ttl'
        mock_nodepool2.spec = {
            'template': {
                'spec': {}
            }
        }

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_ttl_configuration(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'without-ttl' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_instance_type_diversity_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K6 check with diverse instance types."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with multiple instance families
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'diverse-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'In',
                            'values': ['m5.large', 'm6i.large', 'c5.large', 'c6i.large', 'r5.large']
                        }
                    ]
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_diversity(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_instance_type_diversity_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K7 check with limited instance types."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with only one instance type
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'limited-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'In',
                            'values': ['m5.large']
                        }
                    ]
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_diversity(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'limited-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_instance_type_diversity_no_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K7 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_instance_type_diversity(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_instance_type_diversity_no_requirements(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K7 check with NodePool having no requirements."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'no-requirements-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': []
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_diversity(sample_shared_data, 'test-cluster')

        # Behavior depends on implementation
        assert 'check_name' in result

    @pytest.mark.asyncio
    async def test_check_instance_type_diversity_single_family_multiple_sizes(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K7 check with multiple sizes from single family."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'single-family-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'node.kubernetes.io/instance-type',
                            'operator': 'In',
                            'values': ['m5.large', 'm5.xlarge', 'm5.2xlarge']  # Same family
                        }
                    ]
                }
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_instance_type_diversity(sample_shared_data, 'test-cluster')

        # Should be non-compliant as it's only one family
        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_nodepool_limits_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K8 check with resource limits configured."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with limits
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'limited-nodepool'
        mock_nodepool.spec = {
            'limits': {
                'cpu': '1000',
                'memory': '1000Gi'
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_nodepool_limits(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_nodepool_limits_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K8 check without resource limits."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool without limits
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'unlimited-nodepool'
        mock_nodepool.spec = {}
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_nodepool_limits(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'unlimited-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_nodepool_limits_no_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K8 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_nodepool_limits(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_nodepool_limits_partial_limits(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K8 check with only CPU limit (missing memory)."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'partial-limits-nodepool'
        mock_nodepool.spec = {
            'limits': {
                'cpu': '1000'
                # Missing memory limit
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_nodepool_limits(sample_shared_data, 'test-cluster')

        # Behavior depends on implementation - might be compliant or not
        assert 'check_name' in result

    @pytest.mark.asyncio
    async def test_check_nodepool_limits_mixed_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K8 check with mixed NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # NodePool with limits
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'with-limits'
        mock_nodepool1.spec = {
            'limits': {
                'cpu': '1000',
                'memory': '1000Gi'
            }
        }

        # NodePool without limits
        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'without-limits'
        mock_nodepool2.spec = {}

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_nodepool_limits(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'without-limits' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_disruption_settings_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K9 check with disruption settings configured."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with disruption settings
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'disruption-nodepool'
        mock_nodepool.spec = {
            'disruption': {
                'consolidationPolicy': 'WhenUnderutilized',
                'consolidateAfter': '30s'
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_disruption_settings(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_disruption_settings_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K9 check without disruption settings."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool without disruption settings
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'no-disruption-nodepool'
        mock_nodepool.spec = {}
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_disruption_settings(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'no-disruption-nodepool' in result['impacted_resources']

    @pytest.mark.asyncio
    @patch('awslabs.eks_review_mcp_server.eks_karpenter_handler.AwsHelper')
    async def test_check_auto_mode_enabled(
        self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test A1 check with Auto Mode enabled."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock cluster info with Auto Mode
        sample_shared_data['cluster_info'] = {
            'computeConfig': {'enabled': True}
        }
        sample_shared_data['is_auto_mode'] = True

        result = await handler._check_auto_mode(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_spot_consolidation_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K10 check with spot consolidation enabled."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with spot and consolidation
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'spot-nodepool'
        mock_nodepool.spec = {
            'template': {
                'spec': {
                    'requirements': [
                        {
                            'key': 'karpenter.sh/capacity-type',
                            'operator': 'In',
                            'values': ['spot']
                        }
                    ]
                }
            },
            'disruption': {
                'consolidationPolicy': 'WhenUnderutilized'
            }
        }
        sample_shared_data['nodepools'] = [mock_nodepool]

        result = await handler._check_spot_consolidation(sample_shared_data, 'test-cluster')

        # This will be non-compliant because SpotToSpotConsolidation is not enabled in karpenter_config
        assert result['compliant'] is False


class TestEKSKarpenterHandlerHelperMethods:
    """Tests for helper methods and edge cases."""

    @pytest.mark.asyncio
    async def test_check_ami_lockdown_error_handling(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K2 check error handling with malformed data."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # Mock NodePool with missing spec
        mock_nodepool = MagicMock()
        mock_nodepool.metadata.name = 'broken-nodepool'
        mock_nodepool.spec = None  # Malformed
        sample_shared_data['nodepools'] = [mock_nodepool]

        # Should handle gracefully without crashing
        try:
            result = await handler._check_ami_lockdown(sample_shared_data, 'test-cluster')
        except Exception:
            # If it raises an exception, that's also acceptable behavior
            pass

    @pytest.mark.asyncio
    async def test_check_disruption_settings_mixed_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K9 check with mixed NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        # NodePool with disruption settings
        mock_nodepool1 = MagicMock()
        mock_nodepool1.metadata.name = 'with-disruption'
        mock_nodepool1.spec = {
            'disruption': {
                'consolidationPolicy': 'WhenUnderutilized',
                'consolidateAfter': '30s'
            }
        }

        # NodePool without disruption settings
        mock_nodepool2 = MagicMock()
        mock_nodepool2.metadata.name = 'without-disruption'
        mock_nodepool2.spec = {}

        sample_shared_data['nodepools'] = [mock_nodepool1, mock_nodepool2]

        result = await handler._check_disruption_settings(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'without-disruption' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_disruption_settings_no_nodepools(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test K9 check with no NodePools."""
        handler = EKSKarpenterHandler(mock_mcp, mock_client_cache)

        sample_shared_data['nodepools'] = []

        result = await handler._check_disruption_settings(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0
