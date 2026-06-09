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
"""Tests for the EKSClusterAutoscalerHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler import EKSClusterAutoscalerHandler
from awslabs.eks_review_mcp_server.models import ClusterAutoscalerCheckResponse
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
def mock_ec2_client():
    """Create a mock EC2 client."""
    return MagicMock()


@pytest.fixture
def mock_autoscaling_client():
    """Create a mock Auto Scaling client."""
    return MagicMock()


@pytest.fixture
def mock_k8s_client():
    """Create a mock K8s client."""
    return MagicMock()


@pytest.fixture
def sample_cluster_info():
    """Create sample cluster info for testing."""
    return {
        'name': 'test-cluster',
        'version': '1.28',
        'arn': 'arn:aws:eks:us-west-2:123456789012:cluster/test-cluster',
        'resourcesVpcConfig': {
            'subnetIds': ['subnet-1', 'subnet-2'],
            'securityGroupIds': ['sg-1']
        }
    }



@pytest.fixture
def sample_ca_deployment():
    """Create a sample Cluster Autoscaler deployment."""
    deployment = MagicMock()
    deployment.metadata.name = 'cluster-autoscaler'
    deployment.metadata.namespace = 'kube-system'
    deployment.spec.template.spec = {
        'containers': [
            {
                'name': 'cluster-autoscaler',
                'image': 'k8s.gcr.io/autoscaling/cluster-autoscaler:v1.28.0',
                'command': ['./cluster-autoscaler'],
                'args': [
                    '--v=4',
                    '--cloud-provider=aws',
                    '--node-group-auto-discovery=asg:tag=k8s.io/cluster-autoscaler/enabled,k8s.io/cluster-autoscaler/test-cluster',
                    '--expander=least-waste',
                    '--scan-interval=10s',
                    '--scale-down-enabled=true'
                ],
                'resources': {
                    'limits': {'cpu': '100m', 'memory': '300Mi'},
                    'requests': {'cpu': '100m', 'memory': '300Mi'}
                },
                'env': []
            }
        ]
    }
    return deployment



@pytest.fixture
def sample_managed_node_group():
    """Create a sample managed node group."""
    return {
        'nodegroupName': 'test-nodegroup',
        'nodegroupArn': 'arn:aws:eks:us-west-2:123456789012:nodegroup/test-cluster/test-nodegroup/abc',
        'clusterName': 'test-cluster',
        'version': '1.28',
        'capacityType': 'ON_DEMAND',
        'instanceTypes': ['t3.medium'],
        'scalingConfig': {
            'minSize': 1,
            'maxSize': 10,
            'desiredSize': 3
        },
        'tags': {
            'k8s.io/cluster-autoscaler/enabled': 'true',
            'k8s.io/cluster-autoscaler/test-cluster': 'owned'
        },
        'labels': {'environment': 'test'},
        'taints': [],
        'amiType': 'AL2_x86_64',
        'nodeRole': 'arn:aws:iam::123456789012:role/test-node-role',
        'resources': {
            'autoScalingGroups': [
                {'name': 'eks-test-nodegroup-abc'}
            ]
        }
    }


@pytest.fixture
def sample_self_managed_asg():
    """Create a sample self-managed Auto Scaling Group."""
    return {
        'AutoScalingGroupName': 'test-self-managed-asg',
        'MinSize': 1,
        'MaxSize': 5,
        'DesiredCapacity': 2,
        'AvailabilityZones': ['us-west-2a', 'us-west-2b'],
        'LaunchTemplate': {
            'LaunchTemplateId': 'lt-123',
            'LaunchTemplateName': 'test-template',
            'Version': '1'
        },
        'Tags': [
            {'Key': 'k8s.io/cluster-autoscaler/enabled', 'Value': 'true'},
            {'Key': 'k8s.io/cluster-autoscaler/test-cluster', 'Value': 'owned'},
            {'Key': 'Name', 'Value': 'test-self-managed-asg'}
        ]
    }



class TestEKSClusterAutoscalerHandlerInit:
    """Tests for the EKSClusterAutoscalerHandler class initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization of EKSClusterAutoscalerHandler."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache
        assert handler.check_registry is not None
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_cluster_autoscaler_best_practices'

    def test_load_check_registry(self, mock_mcp, mock_client_cache):
        """Test that check registry is loaded correctly."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)
        
        all_checks = handler._get_all_checks()
        assert 'C1' in all_checks
        assert 'C2' in all_checks
        assert 'C14' in all_checks
        assert all_checks['C1']['name'] == 'Cluster Autoscaler version matches cluster version'

    def test_get_check_info(self, mock_mcp, mock_client_cache):
        """Test getting check information by ID."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)
        
        check_info = handler._get_check_info('C1')
        assert check_info['name'] == 'Cluster Autoscaler version matches cluster version'
        assert check_info['category'] == 'Version Compatibility'
        assert check_info['severity'] == 'High'

    @pytest.mark.asyncio
    async def test_check_cluster_autoscaler_connection_error(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_cluster_autoscaler_best_practices with a connection error."""
        mock_client_cache.get_client.side_effect = Exception('Failed to connect to cluster')

        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is True
        assert 'Failed to connect to cluster' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] == 'Connection Error'
        assert result.check_results[0]['compliant'] is False


    @pytest.mark.asyncio
    async def test_check_cluster_autoscaler_auto_mode_early_exit(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test early exit for EKS Auto Mode clusters."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        # Mock _initialize_clients to return Auto Mode data
        auto_mode_data = {
            'skip_ca_checks': True,
            'is_auto_mode': True,
            'auto_mode_features': {
                'compute_enabled': True,
                'storage_enabled': True,
                'elastic_load_balancing_enabled': False
            }
        }
        
        mock_clients = {
            'eks': MagicMock(),
            'ec2': MagicMock(),
            'autoscaling': MagicMock(),
            'k8s': MagicMock(),
            'shared_data': auto_mode_data
        }
        
        handler._initialize_clients = AsyncMock(return_value=mock_clients)
        handler._get_cluster_and_nodegroup_info = AsyncMock(return_value=auto_mode_data)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is True
        assert 'Auto Mode' in result.summary
        assert 'not applicable' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] is not None
        assert result.check_results[0]['compliant'] is True



class TestEKSClusterAutoscalerHandlerInitialization:
    """Tests for client initialization and data collection."""

    @pytest.mark.asyncio
    async def test_initialize_clients_success(self, mock_mcp, mock_client_cache):
        """Test successful client initialization."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        mock_asg = MagicMock()
        mock_k8s = MagicMock()

        with patch('awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler.AwsHelper.create_boto3_client') as mock_create:
            mock_create.side_effect = [mock_eks, mock_ec2, mock_asg]
            mock_client_cache.get_client.return_value = mock_k8s

            clients = await handler._initialize_clients('test-cluster', 'us-west-2', 'kube-system')

            assert clients is not None
            assert clients['eks'] == mock_eks
            assert clients['ec2'] == mock_ec2
            assert clients['autoscaling'] == mock_asg
            assert clients['k8s'] == mock_k8s
            
            # Verify AWS clients were created with correct service names
            assert mock_create.call_count == 3
            calls = [call[0][0] for call in mock_create.call_args_list]
            assert 'eks' in calls
            assert 'ec2' in calls
            assert 'autoscaling' in calls

    @pytest.mark.asyncio
    async def test_initialize_clients_eks_failure(self, mock_mcp, mock_client_cache):
        """Test client initialization with EKS client failure."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        with patch('awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler.AwsHelper.create_boto3_client') as mock_create:
            mock_create.side_effect = Exception('EKS client creation failed')

            clients = await handler._initialize_clients('test-cluster', 'us-west-2', 'kube-system')

            assert clients is None
            mock_create.assert_called_once_with('eks', region_name='us-west-2')

    @pytest.mark.asyncio
    async def test_initialize_clients_k8s_failure(self, mock_mcp, mock_client_cache):
        """Test client initialization with Kubernetes client failure."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_client_cache.get_client.side_effect = Exception('K8s client creation failed')

        with patch('awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler.AwsHelper.create_boto3_client') as mock_create:
            mock_create.side_effect = [MagicMock(), MagicMock(), MagicMock()]

            clients = await handler._initialize_clients('test-cluster', 'us-west-2', 'kube-system')

            assert clients is None



class TestEKSClusterAutoscalerHandlerVersionCheck:
    """Tests for C1: Version compatibility check."""

    @pytest.mark.asyncio
    async def test_check_version_compatibility_matching(
        self, mock_mcp, mock_client_cache, sample_ca_deployment, sample_cluster_info
    ):
        """Test C1 check with matching CA and cluster versions."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        shared_data = {
            'cluster_version': '1.28',
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': sample_ca_deployment
            }]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) > 0
        assert 'kube-system/cluster-autoscaler' in result['impacted_resources']
        assert '1.28' in result['details']

    @pytest.mark.asyncio
    async def test_check_version_compatibility_mismatch(
        self, mock_mcp, mock_client_cache, sample_ca_deployment
    ):
        """Test C1 check with mismatched CA and cluster versions."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        # Cluster is 1.29 but CA is 1.28
        shared_data = {
            'cluster_version': '1.29',
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': sample_ca_deployment
            }]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert len(result['impacted_resources']) > 0
        assert 'kube-system/cluster-autoscaler' in result['impacted_resources'][0]
        assert '1.28' in result['impacted_resources'][0]
        assert '1.29' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_check_version_compatibility_no_ca_deployment(
        self, mock_mcp, mock_client_cache
    ):
        """Test C1 check when no CA deployment is found."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        # Mock K8s client that returns no deployments
        mock_k8s_client = MagicMock()
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        shared_data = {
            'cluster_version': '1.28',
            'ca_deployments': []
        }

        clients = {
            'shared_data': shared_data,
            'k8s': mock_k8s_client  # Provide k8s client for fallback
        }

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 0
        assert 'No Cluster Autoscaler deployment found' in result['details']
        
        # Verify fallback to K8s API was attempted
        mock_k8s_client.list_resources.assert_called_once_with(
            kind='Deployment',
            api_version='apps/v1',
            namespace='kube-system'
        )


    @pytest.mark.asyncio
    async def test_check_version_compatibility_invalid_image_format(
        self, mock_mcp, mock_client_cache
    ):
        """Test C1 check with invalid image format (no version tag)."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        deployment = MagicMock()
        deployment.metadata.name = 'cluster-autoscaler'
        deployment.metadata.namespace = 'kube-system'
        deployment.spec.template.spec = {
            'containers': [{
                'name': 'cluster-autoscaler',
                'image': 'k8s.gcr.io/autoscaling/cluster-autoscaler:latest'  # No version
            }]
        }

        shared_data = {
            'cluster_version': '1.28',
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': deployment
            }]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'cannot determine CA version' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_check_version_compatibility_fallback_to_k8s_api(
        self, mock_mcp, mock_client_cache, sample_ca_deployment
    ):
        """Test C1 check falls back to K8s API when shared_data is not available."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = MagicMock()
        mock_response = MagicMock()
        mock_response.items = [sample_ca_deployment]
        mock_k8s_client.list_resources.return_value = mock_response

        shared_data = {
            'cluster_version': '1.28',
            'ca_deployments': []  # Empty, should trigger fallback
        }

        clients = {
            'shared_data': shared_data,
            'k8s': mock_k8s_client
        }

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        # Verify K8s API was called
        mock_k8s_client.list_resources.assert_called_once_with(
            kind='Deployment',
            api_version='apps/v1',
            namespace='kube-system'
        )

        assert result['compliant'] is True

    @pytest.mark.asyncio
    async def test_check_version_compatibility_error_handling(
        self, mock_mcp, mock_client_cache
    ):
        """Test C1 check error handling."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        shared_data = {
            'cluster_version': None  # Missing cluster version
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_version_compatibility(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'Failed to get cluster version' in result['details']



class TestEKSClusterAutoscalerHandlerAutoDiscoveryCheck:
    """Tests for C2: Auto-discovery enabled check."""

    @pytest.mark.asyncio
    async def test_check_auto_discovery_enabled(
        self, mock_mcp, mock_client_cache, sample_ca_deployment
    ):
        """Test C2 check with auto-discovery enabled."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        shared_data = {
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': sample_ca_deployment
            }]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_auto_discovery_enabled(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is True
        assert 'kube-system/cluster-autoscaler' in result['impacted_resources']
        assert 'Auto-discovery is enabled' in result['details']

    @pytest.mark.asyncio
    async def test_check_auto_discovery_disabled(
        self, mock_mcp, mock_client_cache
    ):
        """Test C2 check with auto-discovery disabled."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        deployment = MagicMock()
        deployment.metadata.name = 'cluster-autoscaler'
        deployment.metadata.namespace = 'kube-system'
        deployment.spec.template.spec = {
            'containers': [{
                'name': 'cluster-autoscaler',
                'image': 'k8s.gcr.io/autoscaling/cluster-autoscaler:v1.28.0',
                'command': ['./cluster-autoscaler'],
                'args': [
                    '--v=4',
                    '--cloud-provider=aws',
                    # No auto-discovery flag
                    '--nodes=1:10:test-asg'
                ]
            }]
        }

        shared_data = {
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': deployment
            }]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_auto_discovery_enabled(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'kube-system/cluster-autoscaler' in result['impacted_resources'][0]
        assert 'auto-discovery not enabled' in result['impacted_resources'][0]

    @pytest.mark.asyncio
    async def test_check_auto_discovery_no_ca_deployment(
        self, mock_mcp, mock_client_cache
    ):
        """Test C2 check when no CA deployment is found."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        # Mock K8s client that returns no deployments
        mock_k8s_client = MagicMock()
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        shared_data = {'ca_deployments': []}
        clients = {
            'shared_data': shared_data,
            'k8s': mock_k8s_client  # Provide k8s client for fallback
        }

        result = await handler._check_auto_discovery_enabled(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'No Cluster Autoscaler deployment found' in result['details']
        
        # Verify fallback to K8s API was attempted
        mock_k8s_client.list_resources.assert_called_once_with(
            kind='Deployment',
            api_version='apps/v1',
            namespace='kube-system'
        )



class TestEKSClusterAutoscalerHandlerNodeGroupTagsCheck:
    """Tests for C3: Node group auto-discovery tags check."""

    @pytest.mark.asyncio
    async def test_check_node_group_tags_all_compliant(
        self, mock_mcp, mock_client_cache, sample_managed_node_group, sample_self_managed_asg
    ):
        """Test C3 check with all node groups having proper tags."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        managed_ng = {
            'type': 'managed',
            'name': 'test-nodegroup',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'true',
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        self_managed_ng = {
            'type': 'self_managed',
            'name': 'test-self-managed-asg',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'true',
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        shared_data = {
            'managed_node_groups': [managed_ng],
            'self_managed_node_groups': [self_managed_ng]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 2
        assert 'Managed: test-nodegroup' in result['impacted_resources']
        assert 'Self-managed: test-self-managed-asg' in result['impacted_resources']
        assert '1 managed, 1 self-managed' in result['details']

    @pytest.mark.asyncio
    async def test_check_node_group_tags_missing_enabled_tag(
        self, mock_mcp, mock_client_cache
    ):
        """Test C3 check with missing enabled tag."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        managed_ng = {
            'type': 'managed',
            'name': 'test-nodegroup',
            'tags': {
                # Missing enabled tag
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        shared_data = {
            'managed_node_groups': [managed_ng],
            'self_managed_node_groups': []
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'Managed: test-nodegroup' in result['impacted_resources']
        assert 'without proper auto-discovery tags' in result['details']

    @pytest.mark.asyncio
    async def test_check_node_group_tags_missing_cluster_tag(
        self, mock_mcp, mock_client_cache
    ):
        """Test C3 check with missing cluster tag."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        self_managed_ng = {
            'type': 'self_managed',
            'name': 'test-asg',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'true'
                # Missing cluster-specific tag
            }
        }

        shared_data = {
            'managed_node_groups': [],
            'self_managed_node_groups': [self_managed_ng]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'Self-managed: test-asg' in result['impacted_resources']


    @pytest.mark.asyncio
    async def test_check_node_group_tags_mixed_compliance(
        self, mock_mcp, mock_client_cache
    ):
        """Test C3 check with mixed compliance across node groups."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        compliant_ng = {
            'type': 'managed',
            'name': 'compliant-ng',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'true',
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        non_compliant_ng = {
            'type': 'managed',
            'name': 'non-compliant-ng',
            'tags': {}  # No tags
        }

        shared_data = {
            'managed_node_groups': [compliant_ng, non_compliant_ng],
            'self_managed_node_groups': []
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'Managed: non-compliant-ng' in result['impacted_resources']
        assert 'Managed: compliant-ng' not in result['impacted_resources']
        assert '1 node groups without proper auto-discovery tags (out of 2 total)' in result['details']

    @pytest.mark.asyncio
    async def test_check_node_group_tags_no_node_groups(
        self, mock_mcp, mock_client_cache
    ):
        """Test C3 check when no node groups are found."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        shared_data = {
            'managed_node_groups': [],
            'self_managed_node_groups': []
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'No node groups found' in result['details']

    @pytest.mark.asyncio
    async def test_check_node_group_tags_self_managed_wrong_value(
        self, mock_mcp, mock_client_cache
    ):
        """Test C3 check with self-managed ASG having wrong enabled tag value."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        self_managed_ng = {
            'type': 'self_managed',
            'name': 'test-asg',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'false',  # Wrong value
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        shared_data = {
            'managed_node_groups': [],
            'self_managed_node_groups': [self_managed_ng]
        }

        clients = {'shared_data': shared_data}

        result = await handler._check_node_group_tags(clients, 'test-cluster', 'kube-system')

        assert result['compliant'] is False
        assert 'Self-managed: test-asg' in result['impacted_resources']



class TestEKSClusterAutoscalerHandlerAlternativeAutoscaling:
    """Tests for alternative autoscaling detection (Karpenter, Auto Mode)."""

    @pytest.mark.asyncio
    async def test_check_for_karpenter_found(
        self, mock_mcp, mock_client_cache
    ):
        """Test detection of Karpenter deployment."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = MagicMock()
        mock_deployment = MagicMock()
        mock_deployment.metadata.name = 'karpenter'
        mock_response = MagicMock()
        mock_response.items = [mock_deployment]
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_for_karpenter(mock_k8s_client, 'karpenter')

        assert result is True
        mock_k8s_client.list_resources.assert_called_once_with(
            kind='Deployment',
            api_version='apps/v1',
            namespace='karpenter'
        )

    @pytest.mark.asyncio
    async def test_check_for_karpenter_not_found(
        self, mock_mcp, mock_client_cache
    ):
        """Test when Karpenter is not deployed."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = MagicMock()
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s_client.list_resources.return_value = mock_response

        result = await handler._check_for_karpenter(mock_k8s_client, 'karpenter')

        assert result is False

    @pytest.mark.asyncio
    async def test_check_for_karpenter_error_handling(
        self, mock_mcp, mock_client_cache
    ):
        """Test Karpenter detection error handling."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = MagicMock()
        mock_k8s_client.list_resources.side_effect = Exception('API error')

        result = await handler._check_for_karpenter(mock_k8s_client, 'karpenter')

        assert result is False

    @pytest.mark.asyncio
    async def test_check_for_auto_mode_enabled(
        self, mock_mcp, mock_client_cache, sample_cluster_info
    ):
        """Test detection of EKS Auto Mode."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        cluster_info = sample_cluster_info.copy()
        cluster_info['computeConfig'] = {'enabled': True}
        mock_eks_client.describe_cluster.return_value = {'cluster': cluster_info}

        result = await handler._check_for_auto_mode(mock_eks_client, 'test-cluster')

        assert result is True
        mock_eks_client.describe_cluster.assert_called_once_with(name='test-cluster')

    @pytest.mark.asyncio
    async def test_check_for_auto_mode_disabled(
        self, mock_mcp, mock_client_cache, sample_cluster_info
    ):
        """Test when Auto Mode is not enabled."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        cluster_info = sample_cluster_info.copy()
        cluster_info['computeConfig'] = {'enabled': False}
        mock_eks_client.describe_cluster.return_value = {'cluster': cluster_info}

        result = await handler._check_for_auto_mode(mock_eks_client, 'test-cluster')

        assert result is False

    @pytest.mark.asyncio
    async def test_check_for_auto_mode_with_cached_data(
        self, mock_mcp, mock_client_cache
    ):
        """Test Auto Mode detection using cached cluster info."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        shared_data = {
            'cluster_info': {
                'computeConfig': {'enabled': True}
            }
        }

        result = await handler._check_for_auto_mode(mock_eks_client, 'test-cluster', shared_data)

        assert result is True
        # Verify API was not called (used cached data)
        mock_eks_client.describe_cluster.assert_not_called()



class TestEKSClusterAutoscalerHandlerHelperMethods:
    """Tests for helper methods."""

    def test_create_check_result(self, mock_mcp, mock_client_cache):
        """Test creating a standardized check result."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        result = handler._create_check_result(
            'C1',
            True,
            ['resource1', 'resource2'],
            'Test details'
        )

        assert result['check_name'] == 'Cluster Autoscaler version matches cluster version'
        assert result['compliant'] is True
        assert result['impacted_resources'] == ['resource1', 'resource2']
        assert result['details'] == 'Test details'

    def test_create_check_result_non_compliant(self, mock_mcp, mock_client_cache):
        """Test creating a non-compliant check result includes remediation."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        result = handler._create_check_result(
            'C1',
            False,
            ['resource1'],
            'Version mismatch'
        )

        assert result['compliant'] is False
        # Remediation should be present for non-compliant checks
        # (actual content depends on JSON file)

    def test_create_check_error_result(self, mock_mcp, mock_client_cache):
        """Test creating an error result."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        result = handler._create_check_error_result('C1', 'Test error message')

        assert result['check_name'] == 'Cluster Autoscaler version matches cluster version'
        assert result['compliant'] is False
        assert result['impacted_resources'] == []
        assert 'Check failed with error: Test error message' in result['details']

    def test_create_error_response(self, mock_mcp, mock_client_cache):
        """Test creating an error response."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        response = handler._create_error_response('test-cluster', 'Connection failed')

        assert isinstance(response, ClusterAutoscalerCheckResponse)
        assert response.isError is True
        assert response.overall_compliant is False
        assert 'Failed to connect to cluster test-cluster' in response.summary
        assert len(response.check_results) == 1
        assert response.check_results[0]['check_name'] == 'Connection Error'
        assert response.check_results[0]['compliant'] is False
        assert 'Connection failed' in response.check_results[0]['details']



class TestEKSClusterAutoscalerHandlerIntegration:
    """Integration-style tests for complete check flow."""

    @pytest.mark.asyncio
    async def test_full_check_with_ca_deployed(
        self, mock_mcp, mock_client_cache, mock_context, sample_ca_deployment
    ):
        """Test full check flow when CA is deployed and compliant."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        # Mock all dependencies
        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        mock_asg = MagicMock()
        mock_k8s = MagicMock()

        managed_ng = {
            'type': 'managed',
            'name': 'test-ng',
            'tags': {
                'k8s.io/cluster-autoscaler/enabled': 'true',
                'k8s.io/cluster-autoscaler/test-cluster': 'owned'
            }
        }

        shared_data = {
            'cluster_version': '1.28',
            'cluster_info': {'version': '1.28'},
            'is_auto_mode': False,
            'skip_ca_checks': False,
            'managed_node_groups': [managed_ng],
            'self_managed_node_groups': [],
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': sample_ca_deployment
            }],
            'ca_config': {
                'auto_discovery_enabled': True,
                'expander_strategy': 'least-waste',
                'scan_interval': '10s'
            }
        }

        mock_clients = {
            'eks': mock_eks,
            'ec2': mock_ec2,
            'autoscaling': mock_asg,
            'k8s': mock_k8s,
            'shared_data': shared_data
        }

        handler._initialize_clients = AsyncMock(return_value=mock_clients)
        handler._get_cluster_and_nodegroup_info = AsyncMock(return_value=shared_data)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        # Verify result structure
        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is False
        assert len(result.check_results) > 0
        
        # Verify C1, C2, C3 checks were run
        check_names = [check['check_name'] for check in result.check_results]
        assert len(check_names) > 0
        # check_ids removed
        # check_ids removed

    @pytest.mark.asyncio
    async def test_full_check_with_karpenter(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full check flow when Karpenter is used instead of CA."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        mock_asg = MagicMock()
        mock_k8s = MagicMock()

        # Mock Karpenter deployment
        mock_karpenter = MagicMock()
        mock_karpenter.metadata.name = 'karpenter'
        mock_response = MagicMock()
        mock_response.items = [mock_karpenter]
        mock_k8s.list_resources.return_value = mock_response

        shared_data = {
            'cluster_version': '1.28',
            'cluster_info': {'version': '1.28'},
            'is_auto_mode': False,
            'skip_ca_checks': False,
            'managed_node_groups': [],
            'self_managed_node_groups': [],
            'ca_deployments': []  # No CA
        }

        mock_clients = {
            'eks': mock_eks,
            'ec2': mock_ec2,
            'autoscaling': mock_asg,
            'k8s': mock_k8s,
            'shared_data': shared_data
        }

        handler._initialize_clients = AsyncMock(return_value=mock_clients)
        handler._get_cluster_and_nodegroup_info = AsyncMock(return_value=shared_data)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is True
        assert 'Karpenter' in result.check_results[0]['details']


    @pytest.mark.asyncio
    async def test_full_check_no_autoscaling_solution(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test full check flow when no autoscaling solution is found."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        mock_asg = MagicMock()
        mock_k8s = MagicMock()

        # No Karpenter or CA
        mock_response = MagicMock()
        mock_response.items = []
        mock_k8s.list_resources.return_value = mock_response

        shared_data = {
            'cluster_version': '1.28',
            'cluster_info': {'version': '1.28'},
            'is_auto_mode': False,
            'skip_ca_checks': False,
            'managed_node_groups': [],
            'self_managed_node_groups': [],
            'ca_deployments': []
        }

        mock_clients = {
            'eks': mock_eks,
            'ec2': mock_ec2,
            'autoscaling': mock_asg,
            'k8s': mock_k8s,
            'shared_data': shared_data
        }

        handler._initialize_clients = AsyncMock(return_value=mock_clients)
        handler._get_cluster_and_nodegroup_info = AsyncMock(return_value=shared_data)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] is not None
        assert result.check_results[0]['compliant'] is False

    @pytest.mark.asyncio
    async def test_full_check_with_partial_failures(
        self, mock_mcp, mock_client_cache, mock_context, sample_ca_deployment
    ):
        """Test full check flow with some checks passing and some failing."""
        handler = EKSClusterAutoscalerHandler(mock_mcp, mock_client_cache)

        mock_eks = MagicMock()
        mock_ec2 = MagicMock()
        mock_asg = MagicMock()
        mock_k8s = MagicMock()

        # Node group without proper tags
        non_compliant_ng = {
            'type': 'managed',
            'name': 'bad-ng',
            'tags': {}  # Missing tags
        }

        shared_data = {
            'cluster_version': '1.28',
            'cluster_info': {'version': '1.28'},
            'is_auto_mode': False,
            'skip_ca_checks': False,
            'managed_node_groups': [non_compliant_ng],
            'self_managed_node_groups': [],
            'ca_deployments': [{
                'name': 'cluster-autoscaler',
                'namespace': 'kube-system',
                'deployment': sample_ca_deployment
            }],
            'ca_config': {
                'auto_discovery_enabled': True
            }
        }

        mock_clients = {
            'eks': mock_eks,
            'ec2': mock_ec2,
            'autoscaling': mock_asg,
            'k8s': mock_k8s,
            'shared_data': shared_data
        }

        handler._initialize_clients = AsyncMock(return_value=mock_clients)
        handler._get_cluster_and_nodegroup_info = AsyncMock(return_value=shared_data)

        result = await handler.check_cluster_autoscaler_best_practices(
            mock_context, cluster_name='test-cluster'
        )

        assert isinstance(result, ClusterAutoscalerCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False
        
        # C1 and C2 should pass, C3 should fail
        passed = sum(1 for check in result.check_results if check['compliant'])
        failed = sum(1 for check in result.check_results if not check['compliant'])
        assert passed > 0
        assert failed > 0
        assert f'{passed} checks passed, {failed} checks failed' in result.summary
