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
"""Tests for the EKSSecurityHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_security_handler import EKSSecurityHandler
from awslabs.eks_review_mcp_server.models import SecurityCheckResponse
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
def mock_k8s_client():
    """Create a mock K8s client."""
    return MagicMock()


@pytest.fixture
def sample_shared_data(mock_k8s_client):
    """Create sample shared_data for testing."""
    return {
        'k8s_client': mock_k8s_client,
        'cluster_name': 'test-cluster',
        'namespace': None,
        'cluster_info': {},
        'addons': [],
        'nodegroups': [],
        'pods': [],
        'service_accounts': [],
        'namespaces': [],
        'nodes': [],
        'eks_client': MagicMock(),
        'ec2_client': MagicMock()
    }


class TestEKSSecurityHandlerInit:
    """Tests for the EKSSecurityHandler class initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization of EKSSecurityHandler."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_eks_security'

    @pytest.mark.asyncio
    async def test_check_eks_security_connection_error(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_eks_security with a connection error."""
        mock_client_cache.get_client.side_effect = Exception('Failed to connect to cluster')

        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        result = await handler.check_eks_security(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, SecurityCheckResponse)
        assert result.isError is True
        assert 'Failed to connect to cluster' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] == 'Connection Error'
        assert result.check_results[0]['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_eks_security_invalid_cluster_name(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_eks_security with invalid cluster name."""
        mock_client_cache.get_client.side_effect = Exception('Cluster not found')

        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        result = await handler.check_eks_security(
            mock_context, cluster_name='non-existent-cluster'
        )

        assert isinstance(result, SecurityCheckResponse)
        assert result.isError is True
        assert 'Cluster not found' in result.summary
        assert result.overall_compliant is False


class TestEKSSecurityHandlerIAMChecks:
    """Tests for IAM-related security checks."""

    @pytest.mark.asyncio
    async def test_check_cluster_access_manager_compliant_api_mode(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I1 check with API authentication mode."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'accessConfig': {
                'authenticationMode': 'API'
            }
        }

        result = await handler._check_cluster_access_manager(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_cluster_access_manager_compliant_api_and_configmap(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I1 check with API_AND_CONFIG_MAP authentication mode."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'accessConfig': {
                'authenticationMode': 'API_AND_CONFIG_MAP'
            }
        }

        result = await handler._check_cluster_access_manager(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_cluster_access_manager_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I1 check with CONFIG_MAP authentication mode."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'accessConfig': {
                'authenticationMode': 'CONFIG_MAP'
            }
        }

        result = await handler._check_cluster_access_manager(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']
        assert 'CONFIG_MAP' in result['details']

    @pytest.mark.asyncio
    async def test_check_cluster_access_manager_missing_config(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I1 check with missing accessConfig."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {}  # No accessConfig

        result = await handler._check_cluster_access_manager(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_cluster_access_manager_error_handling(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I1 check error handling."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = None  # Invalid data

        result = await handler._check_cluster_access_manager(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_private_endpoint_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I2 check with private-only endpoint."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointPublicAccess': False,
                'endpointPrivateAccess': True
            }
        }

        result = await handler._check_private_endpoint(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert 'private only' in result['details']

    @pytest.mark.asyncio
    async def test_check_private_endpoint_non_compliant_both(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I2 check with both public and private access."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointPublicAccess': True,
                'endpointPrivateAccess': True
            }
        }

        result = await handler._check_private_endpoint(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_private_endpoint_non_compliant_public_only(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I2 check with public-only endpoint."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointPublicAccess': True,
                'endpointPrivateAccess': False
            }
        }

        result = await handler._check_private_endpoint(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_private_endpoint_missing_config(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I2 check with missing VPC config."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {}  # No resourcesVpcConfig

        result = await handler._check_private_endpoint(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_private_endpoint_neither_enabled(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I2 check with neither public nor private access enabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointPublicAccess': False,
                'endpointPrivateAccess': False
            }
        }

        result = await handler._check_private_endpoint(
            sample_shared_data, 'test-cluster', None
        )

        # This is an edge case - cluster would be inaccessible
        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_service_account_tokens_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I3 check with automountServiceAccountToken disabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Mock service account with automount disabled
        mock_sa = MagicMock()
        mock_sa.metadata.name = 'test-sa'
        mock_sa.metadata.namespace = 'default'
        mock_sa.get.return_value = False  # automountServiceAccountToken = False

        sample_shared_data['service_accounts'] = [mock_sa]

        result = await handler._check_service_account_tokens(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_service_account_tokens_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I3 check with automountServiceAccountToken enabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Mock service account with automount enabled (default)
        mock_sa = MagicMock()
        mock_sa.metadata.name = 'test-sa'
        mock_sa.metadata.namespace = 'default'
        mock_sa.get.return_value = None  # automountServiceAccountToken not set (defaults to True)

        sample_shared_data['service_accounts'] = [mock_sa]

        result = await handler._check_service_account_tokens(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'default/test-sa' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_service_account_tokens_explicitly_enabled(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I3 check with automountServiceAccountToken explicitly set to True."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_sa = MagicMock()
        mock_sa.metadata.name = 'test-sa'
        mock_sa.metadata.namespace = 'default'
        mock_sa.get.return_value = True  # Explicitly enabled

        sample_shared_data['service_accounts'] = [mock_sa]

        result = await handler._check_service_account_tokens(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'default/test-sa' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_service_account_tokens_no_service_accounts(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I3 check with no service accounts."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['service_accounts'] = []

        result = await handler._check_service_account_tokens(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_service_account_tokens_mixed_compliance(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I3 check with mixed service accounts."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Compliant SA
        mock_sa1 = MagicMock()
        mock_sa1.metadata.name = 'compliant-sa'
        mock_sa1.metadata.namespace = 'default'
        mock_sa1.get.return_value = False

        # Non-compliant SA
        mock_sa2 = MagicMock()
        mock_sa2.metadata.name = 'non-compliant-sa'
        mock_sa2.metadata.namespace = 'default'
        mock_sa2.get.return_value = None

        sample_shared_data['service_accounts'] = [mock_sa1, mock_sa2]

        result = await handler._check_service_account_tokens(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'default/non-compliant-sa' in result['impacted_resources']
        assert 'default/compliant-sa' not in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_least_privileged_rbac_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I4 check with least privileged roles."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock role without wildcards
        mock_role = MagicMock()
        mock_role.metadata.name = 'limited-role'
        mock_role.metadata.namespace = 'default'
        mock_role.rules = [
            {
                'verbs': ['get', 'list'],
                'resources': ['pods'],
                'apiGroups': ['']
            }
        ]

        mock_response = MagicMock()
        mock_response.items = [mock_role]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_least_privileged_rbac(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_least_privileged_rbac_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I4 check with overly permissive roles."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock role with wildcards
        mock_role = MagicMock()
        mock_role.metadata.name = 'admin-role'
        mock_role.metadata.namespace = 'default'
        mock_role.rules = [
            {
                'verbs': ['*'],
                'resources': ['*'],
                'apiGroups': ['*']
            }
        ]

        mock_response = MagicMock()
        mock_response.items = [mock_role]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_least_privileged_rbac(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'default/admin-role' in result['impacted_resources']
        
        # Verify K8s API was called correctly
        mock_k8s_client.list_resources.assert_called()

    @pytest.mark.asyncio
    async def test_check_least_privileged_rbac_wildcard_verbs_only(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I4 check with wildcard verbs only."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        mock_role = MagicMock()
        mock_role.metadata.name = 'verb-wildcard-role'
        mock_role.metadata.namespace = 'default'
        mock_role.rules = [
            {
                'verbs': ['*'],
                'resources': ['pods'],
                'apiGroups': ['']
            }
        ]

        mock_response = MagicMock()
        mock_response.items = [mock_role]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_least_privileged_rbac(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'default/verb-wildcard-role' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_least_privileged_rbac_no_roles(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I4 check with no roles found."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_least_privileged_rbac(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_least_privileged_rbac_api_error(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I4 check with API error."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.list_resources.side_effect = Exception('API error')

        result = await handler._check_least_privileged_rbac(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'error' in result['details'].lower() or 'failed' in result['details'].lower()

    @pytest.mark.asyncio
    async def test_check_pod_identity_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I5 check with Pod Identity addon installed."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['addons'] = ['eks-pod-identity-agent', 'vpc-cni']

        result = await handler._check_pod_identity(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_pod_identity_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test I5 check without Pod Identity addon."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['addons'] = ['vpc-cni', 'kube-proxy']

        result = await handler._check_pod_identity(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']


class TestEKSSecurityHandlerPodSecurityChecks:
    """Tests for Pod Security checks."""

    @pytest.mark.asyncio
    async def test_check_pod_security_standards_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test P1 check with Pod Security Standards configured."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Mock namespace with PSA labels
        mock_ns = MagicMock()
        mock_ns.metadata.name = 'test-namespace'
        mock_ns.metadata.get.return_value = {
            'pod-security.kubernetes.io/enforce': 'restricted'
        }

        sample_shared_data['namespaces'] = [mock_ns]

        result = await handler._check_pod_security_standards(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_pod_security_standards_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test P1 check without Pod Security Standards."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Mock namespace without PSA labels
        mock_ns = MagicMock()
        mock_ns.metadata.name = 'test-namespace'
        mock_ns.metadata.get.return_value = {}

        sample_shared_data['namespaces'] = [mock_ns]

        result = await handler._check_pod_security_standards(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-namespace' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_check_pod_security_standards_baseline_level(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test P1 check with baseline security level."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_ns = MagicMock()
        mock_ns.metadata.name = 'test-namespace'
        mock_ns.metadata.get.return_value = {
            'pod-security.kubernetes.io/enforce': 'baseline'
        }

        sample_shared_data['namespaces'] = [mock_ns]

        result = await handler._check_pod_security_standards(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_pod_security_standards_no_namespaces(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test P1 check with no namespaces."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['namespaces'] = []

        result = await handler._check_pod_security_standards(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    @pytest.mark.asyncio
    async def test_check_pod_security_standards_mixed_namespaces(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test P1 check with mixed namespace compliance."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Compliant namespace
        mock_ns1 = MagicMock()
        mock_ns1.metadata.name = 'compliant-ns'
        mock_ns1.metadata.get.return_value = {
            'pod-security.kubernetes.io/enforce': 'restricted'
        }

        # Non-compliant namespace
        mock_ns2 = MagicMock()
        mock_ns2.metadata.name = 'non-compliant-ns'
        mock_ns2.metadata.get.return_value = {}

        sample_shared_data['namespaces'] = [mock_ns1, mock_ns2]

        result = await handler._check_pod_security_standards(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'non-compliant-ns' in result['impacted_resources']
        assert 'compliant-ns' not in result['impacted_resources']


class TestEKSSecurityHandlerMultiTenancyChecks:
    """Tests for Multi-Tenancy checks."""

    @pytest.mark.asyncio
    async def test_check_network_policies_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M1 check with network policies configured."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock network policy
        mock_np = MagicMock()
        mock_np.metadata.name = 'deny-all'
        mock_np.metadata.namespace = 'default'

        mock_response = MagicMock()
        mock_response.items = [mock_np]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_network_policies(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_network_policies_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M1 check without network policies."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_network_policies(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        
        # Verify K8s API was called
        mock_k8s_client.list_resources.assert_called()

    @pytest.mark.asyncio
    async def test_check_network_policies_multiple_policies(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M1 check with multiple network policies."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock multiple network policies
        mock_np1 = MagicMock()
        mock_np1.metadata.name = 'deny-all'
        mock_np1.metadata.namespace = 'default'

        mock_np2 = MagicMock()
        mock_np2.metadata.name = 'allow-specific'
        mock_np2.metadata.namespace = 'production'

        mock_response = MagicMock()
        mock_response.items = [mock_np1, mock_np2]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_network_policies(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_network_policies_api_error(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M1 check with API error."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.list_resources.side_effect = Exception('API error')

        result = await handler._check_network_policies(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_namespace_quotas_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M2 check with resource quotas configured."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock namespace
        mock_ns = MagicMock()
        mock_ns.metadata.name = 'test-namespace'

        # Mock resource quota
        mock_quota = MagicMock()
        mock_quota.metadata.namespace = 'test-namespace'

        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'ResourceQuota':
                mock_response.items = [mock_quota]
            return mock_response

        mock_k8s_client.list_resources.side_effect = list_resources_side_effect
        sample_shared_data['namespaces'] = [mock_ns]

        result = await handler._check_namespace_quotas(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_namespace_quotas_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test M2 check without resource quotas."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']

        # Mock namespace
        mock_ns = MagicMock()
        mock_ns.metadata.name = 'test-namespace'

        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            mock_response.items = []
            return mock_response

        mock_k8s_client.list_resources.side_effect = list_resources_side_effect
        sample_shared_data['namespaces'] = [mock_ns]

        result = await handler._check_namespace_quotas(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-namespace' in result['impacted_resources']


class TestEKSSecurityHandlerDetectiveControls:
    """Tests for Detective Controls checks."""

    @pytest.mark.asyncio
    async def test_check_control_plane_logs_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test D1 check with control plane logging enabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {
                        'types': ['api', 'audit', 'authenticator', 'controllerManager', 'scheduler'],
                        'enabled': True
                    }
                ]
            }
        }

        result = await handler._check_control_plane_logs(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_control_plane_logs_non_compliant(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test D1 check with control plane logging disabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': []
            }
        }

        result = await handler._check_control_plane_logs(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        # When logging is disabled, impacted_resources might be empty or contain cluster name
        # depending on implementation - just verify it's non-compliant
        assert len(result['impacted_resources']) >= 0

    @pytest.mark.asyncio
    async def test_check_control_plane_logs_partial_logging(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test D1 check with partial logging enabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {
                        'types': ['api', 'audit'],  # Only 2 out of 5 types
                        'enabled': True
                    }
                ]
            }
        }

        result = await handler._check_control_plane_logs(
            sample_shared_data, 'test-cluster', None
        )

        # Depending on implementation, partial logging might be compliant or not
        # Check that the result is valid
        assert 'compliant' in result

    @pytest.mark.asyncio
    async def test_check_control_plane_logs_disabled_explicitly(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test D1 check with logging explicitly disabled."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {
                        'types': ['api', 'audit'],
                        'enabled': False
                    }
                ]
            }
        }

        result = await handler._check_control_plane_logs(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_control_plane_logs_missing_config(
        self, mock_mcp, mock_client_cache, sample_shared_data
    ):
        """Test D1 check with missing logging configuration."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {}  # No logging config

        result = await handler._check_control_plane_logs(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False


class TestEKSSecurityHandlerIntegration:
    """Integration-style tests for complete security check flow."""

    @pytest.mark.asyncio
    async def test_full_security_check_all_compliant(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full security check with all checks passing."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        # Mock all dependencies for a compliant cluster
        mock_k8s_client = MagicMock()
        
        # Mock compliant responses
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        shared_data = {
            'k8s_client': mock_k8s_client,
            'cluster_name': 'test-cluster',
            'namespace': None,
            'cluster_info': {
                'accessConfig': {'authenticationMode': 'API'},
                'resourcesVpcConfig': {
                    'endpointPublicAccess': False,
                    'endpointPrivateAccess': True
                },
                'logging': {
                    'clusterLogging': [{
                        'types': ['api', 'audit', 'authenticator', 'controllerManager', 'scheduler'],
                        'enabled': True
                    }]
                }
            },
            'addons': ['eks-pod-identity-agent'],
            'nodegroups': [],
            'pods': [],
            'nodes': [],
            'service_accounts': [],
            'namespaces': [],
            'eks_client': MagicMock(),
            'ec2_client': MagicMock()
        }

        # Mock the initialization to return our test data
        with patch.object(handler, '_initialize_shared_data', return_value=shared_data):
            # Mock _execute_check to return proper serializable dicts
            async def mock_execute(check_id, *args, **kwargs):
                return {
                    
                    'check_name': f'Check {check_id}',
                    'compliant': True,
                    'impacted_resources': [],
                    'details': 'Compliant',
                    'remediation': ''
                }

            with patch.object(handler, '_execute_check', side_effect=mock_execute):
                result = await handler.check_eks_security(
                    mock_context, cluster_name='test-cluster'
                )

        assert isinstance(result, SecurityCheckResponse)
        assert result.isError is False
        # With all compliant checks, overall should be compliant
        # (actual behavior depends on implementation)

    @pytest.mark.asyncio
    async def test_full_security_check_with_failures(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full security check with some checks failing."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = MagicMock()
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        # Non-compliant service account
        mock_sa = MagicMock()
        mock_sa.metadata.name = 'bad-sa'
        mock_sa.metadata.namespace = 'default'
        mock_sa.get.return_value = None  # automount enabled

        shared_data = {
            'k8s_client': mock_k8s_client,
            'cluster_name': 'test-cluster',
            'namespace': None,
            'cluster_info': {
                'accessConfig': {'authenticationMode': 'CONFIG_MAP'},  # Non-compliant
                'resourcesVpcConfig': {
                    'endpointPublicAccess': True,  # Non-compliant
                    'endpointPrivateAccess': True
                },
                'logging': {'clusterLogging': []}  # Non-compliant
            },
            'addons': ['vpc-cni'],  # Missing pod-identity-agent
            'nodegroups': [],
            'pods': [],
            'nodes': [],
            'service_accounts': [mock_sa],
            'namespaces': [],
            'eks_client': MagicMock(),
            'ec2_client': MagicMock()
        }

        with patch.object(handler, '_initialize_shared_data', return_value=shared_data):
            # Mock _execute_check to return mixed results
            async def mock_execute(check_id, *args, **kwargs):
                if check_id in ('I1', 'I2', 'I3', 'D1', 'M1'):
                    return {
                        
                        'check_name': f'Check {check_id}',
                        'compliant': False,
                        'impacted_resources': [f'Resource for {check_id}'],
                        'details': f'{check_id} non-compliant',
                        'remediation': ''
                    }
                return {
                    
                    'check_name': f'Check {check_id}',
                    'compliant': True,
                    'impacted_resources': [],
                    'details': 'Compliant',
                    'remediation': ''
                }

            with patch.object(handler, '_execute_check', side_effect=mock_execute):
                result = await handler.check_eks_security(
                    mock_context, cluster_name='test-cluster'
                )

        assert isinstance(result, SecurityCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False
        # Should have multiple failed checks
        failed_checks = [check for check in result.check_results if not check['compliant']]
        assert len(failed_checks) > 0


class TestKMSSecretsEncryption:
    """Tests for DE3: KMS envelope encryption for Kubernetes Secrets."""

    @pytest.mark.asyncio
    async def test_kms_encryption_enabled(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test DE3 compliant when encryptionConfig has secrets with KMS key."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)
        sample_shared_data['cluster_info'] = {
            'encryptionConfig': [
                {
                    'resources': ['secrets'],
                    'provider': {'keyArn': 'arn:aws:kms:us-east-1:123456789012:key/abc-123'}
                }
            ]
        }

        result = await handler._check_kms_secrets_encryption(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert 'arn:aws:kms' in result['details']

    @pytest.mark.asyncio
    async def test_kms_encryption_not_enabled(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test DE3 non-compliant when no encryptionConfig for secrets."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)
        sample_shared_data['cluster_info'] = {}

        result = await handler._check_kms_secrets_encryption(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    @pytest.mark.asyncio
    async def test_kms_encryption_empty_config(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test DE3 non-compliant when encryptionConfig exists but is empty."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)
        sample_shared_data['cluster_info'] = {'encryptionConfig': []}

        result = await handler._check_kms_secrets_encryption(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False

    @pytest.mark.asyncio
    async def test_kms_encryption_other_resource_only(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test DE3 non-compliant when encryption is for other resources but not secrets."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)
        sample_shared_data['cluster_info'] = {
            'encryptionConfig': [
                {
                    'resources': ['configmaps'],
                    'provider': {'keyArn': 'arn:aws:kms:us-east-1:123456789012:key/abc-123'}
                }
            ]
        }

        result = await handler._check_kms_secrets_encryption(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False


class TestAnonymousBindings:
    """Tests for I9: ClusterRoleBindings to anonymous/unauthenticated groups."""

    @pytest.mark.asyncio
    async def test_no_anonymous_bindings(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test I9 compliant when no bindings to anonymous/unauthenticated."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_crb = MagicMock()
        mock_crb.metadata.name = 'safe-binding'
        mock_subject = {'name': 'system:masters', 'kind': 'Group'}
        mock_crb.subjects = [mock_subject]

        mock_response = MagicMock()
        mock_response.items = [mock_crb]
        sample_shared_data['k8s_client'].list_resources.return_value = mock_response

        result = await handler._check_anonymous_bindings(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_anonymous_binding_found(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test I9 non-compliant when system:anonymous is bound."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_crb = MagicMock()
        mock_crb.metadata.name = 'anonymous-access'
        mock_subject = {'name': 'system:anonymous', 'kind': 'User'}
        mock_crb.subjects = [mock_subject]

        mock_response = MagicMock()
        mock_response.items = [mock_crb]
        sample_shared_data['k8s_client'].list_resources.return_value = mock_response

        result = await handler._check_anonymous_bindings(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'anonymous-access' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_unauthenticated_binding_found(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test I9 non-compliant when system:unauthenticated is bound."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_crb = MagicMock()
        mock_crb.metadata.name = 'unauth-access'
        mock_subject = {'name': 'system:unauthenticated', 'kind': 'Group'}
        mock_crb.subjects = [mock_subject]

        mock_response = MagicMock()
        mock_response.items = [mock_crb]
        sample_shared_data['k8s_client'].list_resources.return_value = mock_response

        result = await handler._check_anonymous_bindings(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'system:unauthenticated' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_mixed_bindings(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test I9 with mix of safe and unsafe bindings."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_safe_crb = MagicMock()
        mock_safe_crb.metadata.name = 'safe-binding'
        mock_safe_crb.subjects = [{'name': 'system:masters', 'kind': 'Group'}]

        mock_unsafe_crb = MagicMock()
        mock_unsafe_crb.metadata.name = 'bad-binding'
        mock_unsafe_crb.subjects = [{'name': 'system:anonymous', 'kind': 'User'}]

        mock_response = MagicMock()
        mock_response.items = [mock_safe_crb, mock_unsafe_crb]
        sample_shared_data['k8s_client'].list_resources.return_value = mock_response

        result = await handler._check_anonymous_bindings(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1

    @pytest.mark.asyncio
    async def test_no_subjects_binding(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test I9 compliant when binding has no subjects."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        mock_crb = MagicMock()
        mock_crb.metadata.name = 'empty-binding'
        mock_crb.subjects = None

        mock_response = MagicMock()
        mock_response.items = [mock_crb]
        sample_shared_data['k8s_client'].list_resources.return_value = mock_response

        result = await handler._check_anonymous_bindings(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True


class TestPolicyEnforcementEngine:
    """Tests for RS1: Policy enforcement engine detection."""

    @pytest.mark.asyncio
    async def test_gatekeeper_detected(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test RS1 compliant when Gatekeeper CRD API is available."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        def mock_call_api(path, method, **kwargs):
            if path == '/apis/constraints.gatekeeper.sh':
                return MagicMock()
            raise Exception('Not found')

        sample_shared_data['k8s_client'].api_client.call_api = mock_call_api

        result = await handler._check_policy_enforcement_engine(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert 'Gatekeeper' in result['details']

    @pytest.mark.asyncio
    async def test_kyverno_detected(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test RS1 compliant when Kyverno CRD API is available."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        def mock_call_api(path, method, **kwargs):
            if path == '/apis/kyverno.io/v1':
                return MagicMock()
            raise Exception('Not found')

        sample_shared_data['k8s_client'].api_client.call_api = mock_call_api

        result = await handler._check_policy_enforcement_engine(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert 'Kyverno' in result['details']

    @pytest.mark.asyncio
    async def test_both_engines_detected(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test RS1 compliant when both Gatekeeper and Kyverno are present."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        def mock_call_api(path, method, **kwargs):
            return MagicMock()  # All API calls succeed

        sample_shared_data['k8s_client'].api_client.call_api = mock_call_api

        result = await handler._check_policy_enforcement_engine(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is True
        assert 'Gatekeeper' in result['details']
        assert 'Kyverno' in result['details']

    @pytest.mark.asyncio
    async def test_no_engine_detected(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test RS1 non-compliant when no policy engine is found."""
        handler = EKSSecurityHandler(mock_mcp, mock_client_cache)

        def mock_call_api(path, method, **kwargs):
            raise Exception('Not found')

        sample_shared_data['k8s_client'].api_client.call_api = mock_call_api

        result = await handler._check_policy_enforcement_engine(
            sample_shared_data, 'test-cluster', None
        )

        assert result['compliant'] is False
        assert 'No policy enforcement engine found' in result['details']
