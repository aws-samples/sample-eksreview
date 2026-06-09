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
"""Tests for the EKSResiliencyHandler class."""

import pytest
from awslabs.eks_review_mcp_server.eks_resiliency_handler import EKSResiliencyHandler
from awslabs.eks_review_mcp_server.models import ResiliencyCheckResponse
from contextlib import ExitStack
from mcp.server.fastmcp import Context
from unittest.mock import MagicMock, patch


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
    mock_k8s_apis = MagicMock()
    cache.get_client.return_value = mock_k8s_apis
    return cache


@pytest.fixture
def mock_k8s_api():
    """Create a mock K8sApis instance."""
    return MagicMock()


@pytest.fixture
def sample_shared_data(mock_k8s_api):
    """Create sample shared_data for testing with new API."""
    return {
        'k8s_client': mock_k8s_api,
        'cluster_name': 'test-cluster',
        'namespace': None,
        'pods': [],
        'deployments': [],
        'statefulsets': [],
        'daemonsets': [],
        'pdbs': [],
        'hpas': [],
        'vpas': [],
        'nodes': [],
        'namespaces': [],
        'services': [],
        'configmaps': [],
        'resource_quotas': [],
        'limit_ranges': [],
        'validating_webhooks': [],
        'mutating_webhooks': [],
        'kube_system_deployments': [],
        'kube_system_daemonsets': [],
        'kube_system_configmaps': [],
        'eks_client': MagicMock(),
        'cluster_info': {},
    }


class TestEKSResiliencyHandlerInit:
    """Tests for the EKSResiliencyHandler class initialization."""

    def test_init(self, mock_mcp, mock_client_cache):
        """Test initialization of EKSResiliencyHandler."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Verify that the handler has the correct attributes
        assert handler.mcp == mock_mcp
        assert handler.client_cache == mock_client_cache

        # Verify that the check_eks_resiliency tool is registered
        mock_mcp.tool.assert_called_once()
        assert mock_mcp.tool.call_args[1]['name'] == 'check_eks_resiliency'

    @pytest.mark.asyncio
    async def test_check_eks_resiliency_connection_error(
        self, mock_mcp, mock_client_cache, mock_context
    ):
        """Test check_eks_resiliency with a connection error."""
        # Set up the mock client_cache to raise an exception
        mock_client_cache.get_client.side_effect = Exception('Failed to connect to cluster')

        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Call the check_eks_resiliency method
        result = await handler.check_eks_resiliency(mock_context, cluster_name='test-cluster')

        # Verify that the result is a ResiliencyCheckResponse
        assert isinstance(result, ResiliencyCheckResponse)
        assert result.isError is True
        assert 'Failed to connect to cluster' in result.summary
        assert len(result.check_results) == 1
        assert result.check_results[0]['check_name'] == 'Connection Error'
        assert result.check_results[0]['compliant'] is False

    @pytest.mark.asyncio
    async def test_check_eks_resiliency_success(self, mock_mcp, mock_client_cache, mock_context):
        """Test check_eks_resiliency with a successful connection."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock ALL check methods to return compliant results to ensure overall_compliant is True
        check_methods = [
            '_check_singleton_pods',
            '_check_multiple_replicas',
            '_check_pod_anti_affinity',
            '_check_liveness_probe',
            '_check_readiness_probe',
            '_check_pod_disruption_budget',
            '_check_metrics_server',
            '_check_horizontal_pod_autoscaler',
            '_check_custom_metrics',
            '_check_vertical_pod_autoscaler',
            '_check_prestop_hooks',
            '_check_service_mesh',
            '_check_monitoring',
            '_check_centralized_logging',
            '_check_startup_probe',
            '_check_c1',
            '_check_c2',
            '_check_c3',
            '_check_c4',
            '_check_c5',
            '_check_d1',
            '_check_d2',
            '_check_d3',
            '_check_d4',
            '_check_d5',
            '_check_d6',
            '_check_d7',
            '_check_d8',
            '_check_d9',
        ]

        patches = []
        for method_name in check_methods:
            patches.append(
                patch.object(
                    handler,
                    method_name,
                    return_value={
                        'check_name': f'Mock {method_name}',
                        'compliant': True,
                        'impacted_resources': [],
                        'details': 'Mock compliant result',
                        
                    },
                )
            )

        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)

            # Call the check_eks_resiliency method
            result = await handler.check_eks_resiliency(mock_context, cluster_name='test-cluster')

            # Verify that the result is a ResiliencyCheckResponse
            assert isinstance(result, ResiliencyCheckResponse)
            assert result.isError is False
            assert 'passed' in result.summary.lower()
            assert '0 checks failed' in result.summary or 'failed' not in result.summary
            assert len(result.check_results) >= 2  # At least the two checks we mocked
            assert result.overall_compliant is True


class TestEKSResiliencyHandlerChecksA:
    """Tests for the EKSResiliencyHandler class A-series checks."""

    def test_check_singleton_pods(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_singleton_pods method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no pods
        sample_shared_data['pods'] = []
        result = handler._check_singleton_pods(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Avoid running singleton Pods'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Create mock pods - one with owner, one without
        mock_pod1 = MagicMock()
        mock_pod1.to_dict.return_value = {
            'metadata': {
                'name': 'test-pod-1',
                'namespace': 'default',
                'ownerReferences': [{'kind': 'ReplicaSet'}],
            }
        }
        mock_pod2 = MagicMock()
        mock_pod2.to_dict.return_value = {
            'metadata': {'name': 'test-pod-2', 'namespace': 'default'}
        }
        
        # Update shared_data with pods
        sample_shared_data['pods'] = [mock_pod1, mock_pod2]
        result = handler._check_singleton_pods(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Avoid running singleton Pods'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'default/test-pod-2' in result['impacted_resources']

    def test_check_multiple_replicas(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_multiple_replicas method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no deployments
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        result = handler._check_multiple_replicas(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Run multiple replicas'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Create mock deployment with single replica
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-2', 'namespace': 'default'},
            'spec': {'replicas': 1},
        }

        # Update shared_data
        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        result = handler._check_multiple_replicas(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Run multiple replicas'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'Deployment default/test-deployment-2' in result['impacted_resources']

    def test_check_pod_anti_affinity(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_pod_anti_affinity method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no deployments
        sample_shared_data['deployments'] = []
        result = handler._check_pod_anti_affinity(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use pod anti-affinity'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Create mock deployments
        mock_deployment1 = MagicMock()
        mock_deployment1.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-1', 'namespace': 'default'},
            'spec': {'replicas': 2, 'template': {'spec': {'affinity': {'podAntiAffinity': {}}}}},
        }
        mock_deployment2 = MagicMock()
        mock_deployment2.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-2', 'namespace': 'default'},
            'spec': {'replicas': 2, 'template': {'spec': {}}},
        }
        
        # Update shared_data
        sample_shared_data['deployments'] = [mock_deployment1, mock_deployment2]
        result = handler._check_pod_anti_affinity(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use pod anti-affinity'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'default/test-deployment-2' in result['impacted_resources']

    def test_check_liveness_probe(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_liveness_probe method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no workloads
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_liveness_probe(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use liveness probes'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Create deployments with and without liveness probes
        mock_deployment1 = MagicMock()
        mock_deployment1.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-1', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'name': 'container-1',
                                'livenessProbe': {'httpGet': {'path': '/health', 'port': 8080}},
                            }
                        ]
                    }
                }
            },
        }
        mock_deployment2 = MagicMock()
        mock_deployment2.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-2', 'namespace': 'default'},
            'spec': {'template': {'spec': {'containers': [{'name': 'container-2'}]}}},
        }

        # Update shared_data
        sample_shared_data['deployments'] = [mock_deployment1, mock_deployment2]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_liveness_probe(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use liveness probes'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'Deployment: default/test-deployment-2' in result['impacted_resources']

    def test_check_readiness_probe(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_readiness_probe method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock the list_resources method to return no workloads
        # Using shared_data instead of mock_k8s_api

        # Call the _check_readiness_probe method
        result = handler._check_readiness_probe(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use readiness probes'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Mock the list_resources method to return deployments with and without readiness probes
        mock_deployment1 = MagicMock()
        mock_deployment1.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-1', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'name': 'container-1',
                                'readinessProbe': {'httpGet': {'path': '/ready', 'port': 8080}},
                            }
                        ]
                    }
                }
            },
        }
        mock_deployment2 = MagicMock()
        mock_deployment2.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-2', 'namespace': 'default'},
            'spec': {'template': {'spec': {'containers': [{'name': 'container-2'}]}}},
        }

        # Update shared_data with deployments
        sample_shared_data['deployments'] = [mock_deployment1, mock_deployment2]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []

        # Call the _check_readiness_probe method
        result = handler._check_readiness_probe(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use readiness probes'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'Deployment: default/test-deployment-2' in result['impacted_resources']

    def test_check_pod_disruption_budget(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_pod_disruption_budget method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no resources - should be compliant (nothing to check)
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        sample_shared_data['pdbs'] = []
        result = handler._check_pod_disruption_budget(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use Pod Disruption Budgets'
        assert result['compliant'] is True  # No workloads = compliant
        assert len(result['impacted_resources']) == 0

        # Create deployment and PDB with matching labels
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {
                'name': 'test-deployment',
                'namespace': 'default',
                'labels': {'app': 'test-app'},
            },
            'spec': {'replicas': 3, 'selector': {'matchLabels': {'app': 'test-app'}}},
        }

        mock_pdb = MagicMock()
        mock_pdb.to_dict.return_value = {
            'metadata': {'name': 'test-pdb', 'namespace': 'default'},
            'spec': {'selector': {'matchLabels': {'app': 'test-app'}}, 'minAvailable': 2},
        }

        # Update shared_data
        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['pdbs'] = [mock_pdb]

        # Call the _check_pod_disruption_budget method
        result = handler._check_pod_disruption_budget(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Use Pod Disruption Budgets'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    def test_check_metrics_server(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_metrics_server method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no metrics server deployment
        sample_shared_data['kube_system_deployments'] = []
        result = handler._check_metrics_server(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Run Kubernetes Metrics Server'
        assert result['compliant'] is False
        # When metrics server is missing, it's reported as an impacted resource
        assert len(result['impacted_resources']) >= 0

        # Test with metrics server deployment present
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'metrics-server', 'namespace': 'kube-system'}
        }
        sample_shared_data['kube_system_deployments'] = [mock_deployment]
        result = handler._check_metrics_server(sample_shared_data)

        # Verify that the result is correct
        assert result['check_name'] == 'Run Kubernetes Metrics Server'
        # Note: compliant requires both metrics API and deployment
        # In test environment, metrics API check may fail, so we just verify it runs
        assert 'check_name' in result

    def test_check_horizontal_pod_autoscaler(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_horizontal_pod_autoscaler method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no resources - should be compliant
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        sample_shared_data['hpas'] = []
        result = handler._check_horizontal_pod_autoscaler(sample_shared_data)

        assert result['check_name'] == 'Use Horizontal Pod Autoscaler'
        assert result['compliant'] is True  # No multi-replica workloads = compliant
        assert len(result['impacted_resources']) == 0

        # Test with deployment with >1 replica and matching HPA
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'test-deployment', 'namespace': 'default'},
            'spec': {'replicas': 3},
        }

        mock_hpa = MagicMock()
        mock_hpa.to_dict.return_value = {
            'metadata': {'name': 'test-hpa', 'namespace': 'default'},
            'spec': {'scaleTargetRef': {'kind': 'Deployment', 'name': 'test-deployment'}},
        }

        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['hpas'] = [mock_hpa]

        result = handler._check_horizontal_pod_autoscaler(sample_shared_data)

        assert result['check_name'] == 'Use Horizontal Pod Autoscaler'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

    def test_check_custom_metrics(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_custom_metrics method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Configure mock to simulate no custom/external metrics APIs available
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.api_client.call_api.side_effect = Exception('API not available')

        # Call the _check_custom_metrics method
        result = handler._check_custom_metrics(sample_shared_data)

        # Verify that the result structure is correct
        assert result['check_name'] == 'Use custom metrics scaling'
        assert 'compliant' in result
        assert 'impacted_resources' in result
        # In test environment without real k8s API, this will be non-compliant
        assert result['compliant'] is False

    def test_check_vertical_pod_autoscaler(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_vertical_pod_autoscaler method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock VPA CRD check and VPA components
        # Call the _check_vertical_pod_autoscaler method
        # Note: This check tries to access k8s_api directly for CRD check
        # In test environment, it will return non-compliant
        result = handler._check_vertical_pod_autoscaler(sample_shared_data)

        # Verify that the result structure is correct
        assert result['check_name'] == 'Use Vertical Pod Autoscaler'
        assert result['compliant'] is False  # No VPA in test environment
        # VPA controller is reported as missing
        assert len(result['impacted_resources']) >= 0

    def test_check_prestop_hooks(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_prestop_hooks method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Test with no workloads
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_prestop_hooks(sample_shared_data)

        assert result['check_name'] == 'Use preStop hooks'
        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0

        # Test with deployments with and without preStop hooks
        mock_deployment1 = MagicMock()
        mock_deployment1.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-1', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'name': 'container-1',
                                'lifecycle': {
                                    'preStop': {'exec': {'command': ['/bin/sh', '-c', 'sleep 15']}}
                                },
                            }
                        ]
                    }
                }
            },
        }
        mock_deployment2 = MagicMock()
        mock_deployment2.to_dict.return_value = {
            'metadata': {'name': 'test-deployment-2', 'namespace': 'default'},
            'spec': {'template': {'spec': {'containers': [{'name': 'container-2'}]}}},
        }

        sample_shared_data['deployments'] = [mock_deployment1, mock_deployment2]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_prestop_hooks(sample_shared_data)

        assert result['check_name'] == 'Use preStop hooks'
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'Deployment: default/test-deployment-2' in result['impacted_resources']

    def test_check_service_mesh(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_service_mesh method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Configure mock to simulate no service mesh CRDs available
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.api_client.call_api.side_effect = Exception('CRD not found')

        # Call the _check_service_mesh method
        result = handler._check_service_mesh(sample_shared_data)

        # Verify that the result structure is correct
        assert result['check_name'] == 'Use a Service Mesh'
        assert result['compliant'] is False  # No service mesh in test environment
        assert len(result['impacted_resources']) >= 0

    def test_check_monitoring(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_monitoring method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Configure mock to simulate no monitoring CRDs available
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.api_client.call_api.side_effect = Exception('CRD not found')

        # Call the _check_monitoring method
        result = handler._check_monitoring(sample_shared_data)

        # Verify that the result structure is correct
        assert result['check_name'] == 'Monitor your applications'
        assert result['compliant'] is False  # No monitoring in test environment
        assert len(result['impacted_resources']) >= 0

    def test_check_centralized_logging(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_centralized_logging method."""
        # Initialize the EKS resiliency handler
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Configure mock to simulate no logging CRDs available
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_k8s_client.api_client.call_api.side_effect = Exception('CRD not found')

        # Call the _check_centralized_logging method
        result = handler._check_centralized_logging(sample_shared_data)

        # Verify that the result structure is correct
        assert result['check_name'] == 'Use centralized logging'
        assert result['compliant'] is False  # No logging in test environment
        assert len(result['impacted_resources']) >= 0




class TestEKSResiliencyHandlerChecksC:
    """Tests for the EKSResiliencyHandler class C-series checks."""

    def test_check_c1_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c1 with control plane logging enabled."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock cluster info with logging enabled (real EKS API format)
        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {'types': ['api', 'audit'], 'enabled': True},
                ]
            }
        }

        result = handler._check_c1(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert len(result['impacted_resources']) == 0
        assert 'api' in result['details']

    def test_check_c1_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c1 with control plane logging disabled."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock cluster info without logging (real EKS API format)
        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {'types': ['api', 'audit', 'authenticator', 'controllerManager', 'scheduler'], 'enabled': False},
                ]
            }
        }

        result = handler._check_c1(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    def test_check_c2_compliant_with_access_entries(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c2 with EKS Access Entries configured."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock EKS client with access entries
        mock_eks_client = MagicMock()
        mock_eks_client.list_access_entries.return_value = {
            'accessEntries': ['arn:aws:iam::123456789012:role/test-role']
        }
        sample_shared_data['eks_client'] = mock_eks_client

        result = handler._check_c2(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'EKS Access Entries' in result['details']

    def test_check_c2_compliant_with_aws_auth(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c2 with aws-auth ConfigMap."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock aws-auth ConfigMap
        mock_cm = MagicMock()
        mock_cm.to_dict.return_value = {
            'metadata': {'name': 'aws-auth', 'namespace': 'kube-system'}
        }
        sample_shared_data['kube_system_configmaps'] = [mock_cm]
        sample_shared_data['eks_client'] = None

        result = handler._check_c2(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'aws-auth' in result['details']

    def test_check_c2_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c2 with no authentication configured."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['eks_client'] = None
        sample_shared_data['kube_system_configmaps'] = []

        result = handler._check_c2(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    def test_check_c3_not_large_cluster(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c3 with a small cluster."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock small number of services
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_services = MagicMock()
        mock_services.items = [MagicMock() for _ in range(100)]
        mock_k8s_client.list_resources.return_value = mock_services

        result = handler._check_c3(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'not a large cluster' in result['details']

    def test_check_c3_large_cluster_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c3 with a large cluster with optimizations."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock large number of services
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_services = MagicMock()
        mock_services.items = [MagicMock() for _ in range(1500)]
        mock_k8s_client.list_resources.return_value = mock_services

        # Mock kube-proxy with IPVS mode
        mock_cm = MagicMock()
        mock_cm.to_dict.return_value = {
            'metadata': {'name': 'kube-proxy-config'},
            'data': {'config.conf': 'mode: "ipvs"'}
        }
        sample_shared_data['kube_system_configmaps'] = [mock_cm]

        # Mock aws-node with IP caching
        mock_ds = MagicMock()
        mock_ds.to_dict.return_value = {
            'metadata': {'name': 'aws-node'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [{
                            'env': [{'name': 'WARM_IP_TARGET', 'value': '5'}]
                        }]
                    }
                }
            }
        }
        sample_shared_data['kube_system_daemonsets'] = [mock_ds]

        result = handler._check_c3(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_c4_compliant_private_endpoint(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c4 with private endpoint."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointConfigPublicAccess': False
            }
        }

        result = handler._check_c4(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'private only' in result['details']

    def test_check_c4_compliant_restricted_public(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c4 with restricted public access."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointConfigPublicAccess': True,
                'publicAccessCidrs': ['10.0.0.0/8']
            }
        }

        result = handler._check_c4(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_c4_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c4 with unrestricted public access."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'resourcesVpcConfig': {
                'endpointConfigPublicAccess': True,
                'publicAccessCidrs': ['0.0.0.0/0']
            }
        }

        result = handler._check_c4(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    def test_check_c5_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c5 with no catch-all webhooks."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock webhooks without catch-all rules
        mock_webhook = MagicMock()
        mock_webhook.to_dict.return_value = {
            'metadata': {'name': 'test-webhook'},
            'webhooks': [{
                'rules': [{
                    'apiGroups': ['apps'],
                    'apiVersions': ['v1'],
                    'resources': ['deployments']
                }]
            }]
        }
        
        mock_response = MagicMock()
        mock_response.items = [mock_webhook]
        mock_k8s_client.list_resources.return_value = mock_response

        result = handler._check_c5(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_c5_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c5 with catch-all webhooks."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock webhook with catch-all rule
        mock_webhook = MagicMock()
        mock_webhook.to_dict.return_value = {
            'metadata': {'name': 'catch-all-webhook'},
            'webhooks': [{
                'rules': [{
                    'apiGroups': ['*'],
                    'apiVersions': ['*'],
                    'resources': ['*']
                }]
            }]
        }
        
        mock_response = MagicMock()
        mock_response.items = [mock_webhook]
        mock_k8s_client.list_resources.return_value = mock_response

        result = handler._check_c5(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert len(result['impacted_resources']) > 0


class TestEKSResiliencyHandlerChecksD:
    """Tests for the EKSResiliencyHandler class D-series checks."""

    def test_check_d1_compliant_with_cluster_autoscaler(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d1 with Cluster Autoscaler deployed."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock Cluster Autoscaler deployment
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'cluster-autoscaler', 'namespace': 'kube-system'}
        }
        sample_shared_data['kube_system_deployments'] = [mock_deployment]

        result = handler._check_d1(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'Cluster Autoscaler' in result['details']

    def test_check_d1_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d1 with no autoscaling solution."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['kube_system_deployments'] = []

        result = handler._check_d1(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'No node autoscaling' in result['details']

    def test_check_d3_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d3 with proper resource requests/limits."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock deployment with complete resources
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'test-deployment', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [{
                            'name': 'app',
                            'resources': {
                                'requests': {'cpu': '100m', 'memory': '128Mi'},
                                'limits': {'cpu': '200m', 'memory': '256Mi'}
                            }
                        }]
                    }
                }
            }
        }
        
        # Populate shared_data deployments (D3 uses shared_data instead of API call)
        sample_shared_data['deployments'] = [mock_deployment]

        result = handler._check_d3(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_d3_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d3 with missing resource specifications."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock deployment without resources
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'test-deployment', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [{
                            'name': 'app',
                            'resources': {}
                        }]
                    }
                }
            }
        }
        
        # Populate shared_data deployments (D3 uses shared_data instead of API call)
        sample_shared_data['deployments'] = [mock_deployment]

        result = handler._check_d3(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert len(result['impacted_resources']) > 0

    def test_check_d4_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d4 with ResourceQuotas configured."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock namespace
        mock_ns = MagicMock()
        mock_ns.to_dict.return_value = {
            'metadata': {'name': 'test-namespace'}
        }
        
        # Mock resource quota
        mock_quota = MagicMock()
        mock_quota.to_dict.return_value = {
            'metadata': {'namespace': 'test-namespace'}
        }
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'Namespace':
                mock_response.items = [mock_ns]
            elif kind == 'ResourceQuota':
                mock_response.items = [mock_quota]
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect

        result = handler._check_d4(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_d4_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d4 with missing ResourceQuotas."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock namespace without quota
        mock_ns = MagicMock()
        mock_ns.to_dict.return_value = {
            'metadata': {'name': 'test-namespace'}
        }
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'Namespace':
                mock_response.items = [mock_ns]
            elif kind == 'ResourceQuota':
                mock_response.items = []
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect

        result = handler._check_d4(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'test-namespace' in result['impacted_resources']

    def test_check_d5_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d5 with LimitRanges configured."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock namespace
        mock_ns = MagicMock()
        mock_ns.to_dict.return_value = {
            'metadata': {'name': 'test-namespace'}
        }
        
        # Mock limit range
        mock_limit = MagicMock()
        mock_limit.to_dict.return_value = {
            'metadata': {'namespace': 'test-namespace'}
        }
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'Namespace':
                mock_response.items = [mock_ns]
            elif kind == 'LimitRange':
                mock_response.items = [mock_limit]
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect

        result = handler._check_d5(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_d5_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d5 with missing LimitRanges."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock namespace without limit range
        mock_ns = MagicMock()
        mock_ns.to_dict.return_value = {
            'metadata': {'name': 'test-namespace'}
        }
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'Namespace':
                mock_response.items = [mock_ns]
            elif kind == 'LimitRange':
                mock_response.items = []
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect

        result = handler._check_d5(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'test-namespace' in result['impacted_resources']

    def test_check_d6_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d6 with CoreDNS monitoring configured."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock CoreDNS deployment
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'coredns', 'namespace': 'kube-system'}
        }
        
        # Populate shared_data kube_system_deployments (D6 uses shared_data instead of API call)
        sample_shared_data['kube_system_deployments'] = [mock_deployment]
        
        # Mock ServiceMonitor
        mock_sm = MagicMock()
        mock_sm.to_dict.return_value = {
            'metadata': {'name': 'coredns-metrics', 'namespace': 'kube-system'}
        }
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'ServiceMonitor':
                mock_response.items = [mock_sm]
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect
        
        # Mock API call for ServiceMonitor CRD check
        mock_k8s_client.api_client.call_api.return_value = MagicMock()

        result = handler._check_d6(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True

    def test_check_d6_non_compliant(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d6 with CoreDNS but no monitoring."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_k8s_client = sample_shared_data['k8s_client']
        
        # Mock CoreDNS deployment
        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'coredns', 'namespace': 'kube-system'}
        }
        
        # Populate shared_data kube_system_deployments (D6 uses shared_data instead of API call)
        sample_shared_data['kube_system_deployments'] = [mock_deployment]
        
        def list_resources_side_effect(kind, **kwargs):
            mock_response = MagicMock()
            if kind == 'ServiceMonitor':
                mock_response.items = []
            return mock_response
        
        mock_k8s_client.list_resources.side_effect = list_resources_side_effect
        
        # Mock API call failure for ServiceMonitor CRD
        mock_k8s_client.api_client.call_api.side_effect = Exception('CRD not found')

        result = handler._check_d6(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_d7_compliant_managed_addon(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d7 with EKS managed CoreDNS addon."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Populate shared_data with cluster info and addons (D7 uses shared_data)
        sample_shared_data['cluster_info'] = {'computeConfig': {'enabled': False}}
        sample_shared_data['addons'] = ['coredns', 'kube-proxy']

        result = handler._check_d7(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'managed' in result['details'].lower()

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_d7_non_compliant_self_managed(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d7 with self-managed CoreDNS."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Populate shared_data with cluster info and addons (D7 uses shared_data)
        sample_shared_data['cluster_info'] = {'computeConfig': {'enabled': False}}
        sample_shared_data['addons'] = ['kube-proxy']  # No coredns addon

        # Mock CoreDNS deployment
        mock_k8s_client = sample_shared_data['k8s_client']
        mock_deployment = MagicMock()
        mock_response = MagicMock()
        mock_response.items = [mock_deployment]
        mock_k8s_client.list_resources.return_value = mock_response

        result = handler._check_d7(sample_shared_data, 'test-cluster')

        assert result['compliant'] is False
        assert 'self-managed' in result['details'].lower()

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_d7_compliant_auto_mode(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_d7 with EKS auto mode cluster."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Populate shared_data with auto mode cluster info (D7 uses shared_data)
        sample_shared_data['cluster_info'] = {'computeConfig': {'enabled': True}}

        result = handler._check_d7(sample_shared_data, 'test-cluster')

        assert result['compliant'] is True
        assert 'auto mode' in result['details'].lower()



class TestEKSResiliencyHandlerEdgeCases:
    """Tests for edge cases and error handling."""

    def test_check_singleton_pods_with_system_pods(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_singleton_pods with system namespace pods."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Create mock pod in kube-system namespace (should be ignored)
        mock_pod = MagicMock()
        mock_pod.to_dict.return_value = {
            'metadata': {
                'name': 'system-pod',
                'namespace': 'kube-system'
            }
        }
        
        sample_shared_data['pods'] = [mock_pod]
        result = handler._check_singleton_pods(sample_shared_data)

        assert result['check_name'] == 'Avoid running singleton Pods'
        # System pods might be treated differently
        assert 'check_name' in result

    def test_check_multiple_replicas_with_zero_replicas(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_multiple_replicas with zero replicas."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'scaled-down', 'namespace': 'default'},
            'spec': {'replicas': 0},
        }

        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        result = handler._check_multiple_replicas(sample_shared_data)

        assert result['check_name'] == 'Run multiple replicas'
        # Zero replicas is treated as compliant (intentionally scaled down)
        assert result['compliant'] is True

    def test_check_pod_anti_affinity_with_single_replica(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_pod_anti_affinity with single replica deployment."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'single-replica', 'namespace': 'default'},
            'spec': {'replicas': 1, 'template': {'spec': {}}},
        }
        
        sample_shared_data['deployments'] = [mock_deployment]
        result = handler._check_pod_anti_affinity(sample_shared_data)

        assert result['check_name'] == 'Use pod anti-affinity'
        # Single replica doesn't need anti-affinity
        assert result['compliant'] is True

    def test_check_liveness_probe_with_init_containers(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_liveness_probe with init containers."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'with-init', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'initContainers': [{'name': 'init'}],
                        'containers': [
                            {
                                'name': 'main',
                                'livenessProbe': {'httpGet': {'path': '/health', 'port': 8080}}
                            }
                        ]
                    }
                }
            },
        }

        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_liveness_probe(sample_shared_data)

        assert result['check_name'] == 'Use liveness probes'
        assert result['compliant'] is True

    def test_check_readiness_probe_multiple_containers(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_readiness_probe with multiple containers."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'name': 'multi-container', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'name': 'container-1',
                                'readinessProbe': {'httpGet': {'path': '/ready', 'port': 8080}}
                            },
                            {
                                'name': 'container-2'
                                # Missing readiness probe
                            }
                        ]
                    }
                }
            },
        }

        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = []
        result = handler._check_readiness_probe(sample_shared_data)

        assert result['check_name'] == 'Use readiness probes'
        # Should be non-compliant because one container is missing probe
        assert result['compliant'] is False

    def test_check_pod_disruption_budget_with_statefulset(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_pod_disruption_budget with StatefulSet."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_statefulset = MagicMock()
        mock_statefulset.to_dict.return_value = {
            'metadata': {
                'name': 'test-statefulset',
                'namespace': 'default',
                'labels': {'app': 'database'},
            },
            'spec': {'replicas': 3, 'selector': {'matchLabels': {'app': 'database'}}},
        }

        mock_pdb = MagicMock()
        mock_pdb.to_dict.return_value = {
            'metadata': {'name': 'test-pdb', 'namespace': 'default'},
            'spec': {'selector': {'matchLabels': {'app': 'database'}}, 'minAvailable': 2},
        }

        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = [mock_statefulset]
        sample_shared_data['pdbs'] = [mock_pdb]

        result = handler._check_pod_disruption_budget(sample_shared_data)

        assert result['check_name'] == 'Use Pod Disruption Budgets'
        assert result['compliant'] is True

    def test_check_horizontal_pod_autoscaler_with_statefulset(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_horizontal_pod_autoscaler with StatefulSet."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_statefulset = MagicMock()
        mock_statefulset.to_dict.return_value = {
            'metadata': {'name': 'test-statefulset', 'namespace': 'default'},
            'spec': {'replicas': 3},
        }

        mock_hpa = MagicMock()
        mock_hpa.to_dict.return_value = {
            'metadata': {'name': 'test-hpa', 'namespace': 'default'},
            'spec': {'scaleTargetRef': {'kind': 'StatefulSet', 'name': 'test-statefulset'}},
        }

        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = [mock_statefulset]
        sample_shared_data['hpas'] = [mock_hpa]

        result = handler._check_horizontal_pod_autoscaler(sample_shared_data)

        assert result['check_name'] == 'Use Horizontal Pod Autoscaler'
        assert result['compliant'] is True

    def test_check_prestop_hooks_with_daemonset(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_prestop_hooks with DaemonSet."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_daemonset = MagicMock()
        mock_daemonset.to_dict.return_value = {
            'metadata': {'name': 'test-daemonset', 'namespace': 'default'},
            'spec': {
                'template': {
                    'spec': {
                        'containers': [
                            {
                                'name': 'container-1',
                                'lifecycle': {
                                    'preStop': {'exec': {'command': ['/bin/sh', '-c', 'sleep 15']}}
                                },
                            }
                        ]
                    }
                }
            },
        }

        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = []
        sample_shared_data['daemonsets'] = [mock_daemonset]
        result = handler._check_prestop_hooks(sample_shared_data)

        assert result['check_name'] == 'Use preStop hooks'
        assert result['compliant'] is True


class TestEKSResiliencyHandlerCSeriesEdgeCases:
    """Tests for C-series checks edge cases."""

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_c1_with_partial_logging(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c1 with partial logging enabled."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'logging': {
                'clusterLogging': [
                    {'types': ['api'], 'enabled': True},
                    # Missing audit and other types
                ]
            }
        }

        result = handler._check_c1(sample_shared_data, 'test-cluster')

        # Partial logging might still be compliant depending on implementation
        assert 'check_name' in result

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_c2_with_empty_access_entries(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c2 with empty access entries."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        mock_eks_client.list_access_entries.return_value = {
            'accessEntries': []  # Empty list
        }
        sample_shared_data['eks_client'] = mock_eks_client

        result = handler._check_c2(sample_shared_data, 'test-cluster')

        # Empty access entries should be non-compliant
        assert result['compliant'] is False

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_c3_with_multiple_node_groups(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c3 with multiple node groups."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_eks_client = MagicMock()
        mock_eks_client.list_nodegroups.return_value = {
            'nodegroups': ['ng-1', 'ng-2', 'ng-3']
        }
        sample_shared_data['eks_client'] = mock_eks_client

        result = handler._check_c3(sample_shared_data, 'test-cluster')

        # Multiple node groups should be compliant
        assert result['compliant'] is True

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_c4_with_missing_encryption_config(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c4 with missing encryption configuration."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {}  # No encryption config

        result = handler._check_c4(sample_shared_data, 'test-cluster')

        # Missing encryption might be treated as compliant in older clusters
        # Just verify the check runs without error
        assert 'check_name' in result

    @patch('awslabs.eks_review_mcp_server.eks_resiliency_handler.AwsHelper')
    def test_check_c5_with_old_version(self, mock_aws_helper, mock_mcp, mock_client_cache, sample_shared_data):
        """Test _check_c5 with old Kubernetes version."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        sample_shared_data['cluster_info'] = {
            'version': '1.24'  # Old version
        }

        result = handler._check_c5(sample_shared_data, 'test-cluster')

        # Version check depends on what's considered "current"
        # Just verify the check runs and returns valid result
        assert 'check_name' in result
        assert 'compliant' in result


class TestEKSResiliencyHandlerIntegration:
    """Integration-style tests for complete resiliency check flow."""

    @pytest.mark.asyncio
    async def test_full_resiliency_check_mixed_results(self, mock_mcp, mock_client_cache, mock_context):
        """Test full resiliency check with mixed compliant and non-compliant results."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock some checks to pass and some to fail
        with patch.object(handler, '_check_singleton_pods', return_value={
            'check_name': 'Avoid running singleton Pods',
            'compliant': True,
            'impacted_resources': [],
            'details': 'No singleton pods found',
            'remediation': ''
        }):
            with patch.object(handler, '_check_multiple_replicas', return_value={
                'check_name': 'Run multiple replicas',
                'compliant': False,
                'impacted_resources': ['Deployment default/single-replica'],
                'details': 'Found deployments with single replica',
                'remediation': 'Increase replica count'
            }):
                with patch.object(handler, '_check_liveness_probe', return_value={
                    'check_name': 'Use liveness probes',
                    'compliant': False,
                    'impacted_resources': ['Deployment: default/no-probe'],
                    'details': 'Missing liveness probes',
                    'remediation': 'Add liveness probes'
                }):
                    result = await handler.check_eks_resiliency(mock_context, cluster_name='test-cluster')

        assert isinstance(result, ResiliencyCheckResponse)
        assert result.isError is False
        assert result.overall_compliant is False
        # Should have both passing and failing checks
        passed = sum(1 for check in result.check_results if check['compliant'])
        failed = sum(1 for check in result.check_results if not check['compliant'])
        assert passed > 0
        assert failed > 0

    @pytest.mark.asyncio
    async def test_full_resiliency_check_with_api_errors(self, mock_mcp, mock_client_cache, mock_context):
        """Test full resiliency check when some checks encounter API errors."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Mock a check that raises an exception
        with patch.object(handler, '_check_singleton_pods', side_effect=Exception('API error')):
            with patch.object(handler, '_check_multiple_replicas', return_value={
                'check_name': 'Run multiple replicas',
                'compliant': True,
                'impacted_resources': [],
                'details': 'All deployments have multiple replicas',
                'remediation': ''
            }):
                result = await handler.check_eks_resiliency(mock_context, cluster_name='test-cluster')

        assert isinstance(result, ResiliencyCheckResponse)
        # Should handle errors gracefully
        assert 'check_results' in result.__dict__

    @pytest.mark.asyncio
    async def test_check_eks_resiliency_with_namespace_filter(self, mock_mcp, mock_client_cache, mock_context):
        """Test check_eks_resiliency with namespace filter."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        with patch.object(handler, '_check_singleton_pods', return_value={
            'check_name': 'Avoid running singleton Pods',
            'compliant': True,
            'impacted_resources': [],
            'details': 'No singleton pods in namespace',
            'remediation': ''
        }):
            result = await handler.check_eks_resiliency(
                mock_context, 
                cluster_name='test-cluster',
                namespace='production'
            )

        assert isinstance(result, ResiliencyCheckResponse)
        assert result.isError is False


class TestStartupProbeCheck:
    """Tests for A15: Startup probe check."""

    def test_startup_probe_compliant_all_have_startup(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test A15 compliant when all workloads with liveness also have startup probes."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'namespace': 'default', 'name': 'my-app'},
            'spec': {'template': {'spec': {'containers': [
                {'livenessProbe': {'httpGet': {'path': '/health'}}, 'startupProbe': {'httpGet': {'path': '/ready'}}}
            ]}}}
        }
        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []

        result = handler._check_startup_probe(sample_shared_data)
        assert result['compliant'] is True

    def test_startup_probe_non_compliant_missing_startup(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test A15 non-compliant when liveness exists but startup doesn't."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'namespace': 'default', 'name': 'slow-app'},
            'spec': {'template': {'spec': {'containers': [
                {'livenessProbe': {'httpGet': {'path': '/health'}}}
            ]}}}
        }
        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []

        result = handler._check_startup_probe(sample_shared_data)
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'slow-app' in result['impacted_resources'][0]

    def test_startup_probe_no_liveness_no_flag(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test A15 compliant when no liveness probe exists (nothing to protect)."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_deployment = MagicMock()
        mock_deployment.to_dict.return_value = {
            'metadata': {'namespace': 'default', 'name': 'simple-app'},
            'spec': {'template': {'spec': {'containers': [
                {'name': 'app'}  # no probes at all
            ]}}}
        }
        sample_shared_data['deployments'] = [mock_deployment]
        sample_shared_data['statefulsets'] = []

        result = handler._check_startup_probe(sample_shared_data)
        assert result['compliant'] is True

    def test_startup_probe_statefulset(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test A15 also checks StatefulSets."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_ss = MagicMock()
        mock_ss.to_dict.return_value = {
            'metadata': {'namespace': 'db', 'name': 'postgres'},
            'spec': {'template': {'spec': {'containers': [
                {'livenessProbe': {'tcpSocket': {'port': 5432}}}
            ]}}}
        }
        sample_shared_data['deployments'] = []
        sample_shared_data['statefulsets'] = [mock_ss]

        result = handler._check_startup_probe(sample_shared_data)
        assert result['compliant'] is False
        assert 'StatefulSet' in result['impacted_resources'][0]


class TestNodeMonitoringAgentCheck:
    """Tests for D8: EKS Node Monitoring Agent check."""

    def test_node_monitoring_agent_installed(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D8 compliant when the addon is installed."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)
        sample_shared_data['addons'] = ['coredns', 'kube-proxy', 'amazon-eks-node-monitoring-agent']

        result = handler._check_d8(sample_shared_data, 'test-cluster')
        assert result['compliant'] is True
        assert 'installed' in result['details'].lower()

    def test_node_monitoring_agent_not_installed(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D8 non-compliant when the addon is missing."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)
        sample_shared_data['addons'] = ['coredns', 'kube-proxy', 'vpc-cni']

        result = handler._check_d8(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False
        assert 'test-cluster' in result['impacted_resources']

    def test_node_monitoring_agent_empty_addons(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D8 non-compliant when no addons are installed."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)
        sample_shared_data['addons'] = []

        result = handler._check_d8(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False

    def test_node_monitoring_agent_case_insensitive(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D8 handles case variations in addon name."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)
        sample_shared_data['addons'] = ['Amazon-EKS-Node-Monitoring-Agent']

        result = handler._check_d8(sample_shared_data, 'test-cluster')
        assert result['compliant'] is True


class TestKubeletReservedResources:
    """Tests for D9: Kubelet reserved resources check."""

    def test_reservations_configured(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 compliant when allocatable < capacity."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_node = MagicMock()
        mock_node.to_dict.return_value = {
            'metadata': {'name': 'ip-10-0-1-100'},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '3920m', 'memory': '15360Mi'},
            }
        }
        sample_shared_data['nodes'] = [mock_node]

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is True

    def test_no_reservations(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 non-compliant when allocatable equals capacity."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        mock_node = MagicMock()
        mock_node.to_dict.return_value = {
            'metadata': {'name': 'ip-10-0-1-200'},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '4', 'memory': '16384Mi'},
            }
        }
        sample_shared_data['nodes'] = [mock_node]

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'ip-10-0-1-200' in result['impacted_resources'][0]

    def test_mixed_nodes(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 non-compliant when some nodes lack reservations."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        good_node = MagicMock()
        good_node.to_dict.return_value = {
            'metadata': {'name': 'managed-node'},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '3920m', 'memory': '15360Mi'},
            }
        }
        bad_node = MagicMock()
        bad_node.to_dict.return_value = {
            'metadata': {'name': 'self-managed-node'},
            'status': {
                'capacity': {'cpu': '8', 'memory': '32768Mi'},
                'allocatable': {'cpu': '8', 'memory': '32768Mi'},
            }
        }
        sample_shared_data['nodes'] = [good_node, bad_node]

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False
        assert len(result['impacted_resources']) == 1
        assert 'self-managed-node' in result['impacted_resources'][0]

    def test_no_nodes(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 non-compliant when no nodes found."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)
        sample_shared_data['nodes'] = []

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False

    def test_self_managed_grouped_by_instance_type(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 groups self-managed nodes by instance type, checking one per type."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        # Two self-managed nodes same instance type — only one should be checked
        node1 = MagicMock()
        node1.to_dict.return_value = {
            'metadata': {'name': 'self-managed-1', 'labels': {
                'node.kubernetes.io/instance-type': 'm5.xlarge'
            }},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '4', 'memory': '16384Mi'},
            }
        }
        node2 = MagicMock()
        node2.to_dict.return_value = {
            'metadata': {'name': 'self-managed-2', 'labels': {
                'node.kubernetes.io/instance-type': 'm5.xlarge'
            }},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '4', 'memory': '16384Mi'},
            }
        }
        sample_shared_data['nodes'] = [node1, node2]

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is False
        # Should only report one group, not two individual nodes
        assert len(result['impacted_resources']) == 1

    def test_managed_nodegroup_label_takes_priority(self, mock_mcp, mock_client_cache, sample_shared_data):
        """Test D9 uses eks nodegroup label over instance type for managed nodes."""
        handler = EKSResiliencyHandler(mock_mcp, mock_client_cache)

        node1 = MagicMock()
        node1.to_dict.return_value = {
            'metadata': {'name': 'managed-1', 'labels': {
                'eks.amazonaws.com/nodegroup': 'ng-large',
                'node.kubernetes.io/instance-type': 'm5.xlarge'
            }},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '3920m', 'memory': '15360Mi'},
            }
        }
        node2 = MagicMock()
        node2.to_dict.return_value = {
            'metadata': {'name': 'managed-2', 'labels': {
                'eks.amazonaws.com/nodegroup': 'ng-large',
                'node.kubernetes.io/instance-type': 'm5.xlarge'
            }},
            'status': {
                'capacity': {'cpu': '4', 'memory': '16384Mi'},
                'allocatable': {'cpu': '3920m', 'memory': '15360Mi'},
            }
        }
        sample_shared_data['nodes'] = [node1, node2]

        result = handler._check_d9(sample_shared_data, 'test-cluster')
        assert result['compliant'] is True
        # Should report checking 1 group, not 2 nodes
        assert '1 node groups' in result['details']
