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

"""Handler for EKS resiliency checks in the EKS MCP Server."""

import json
from pathlib import Path
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_response
from awslabs.eks_review_mcp_server.logging_helper import LogLevel, log_with_request_id
from awslabs.eks_review_mcp_server.models import ResiliencyCheckResponse
from collections import Counter
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, Optional, List


class EKSResiliencyHandler:
    """Handler for EKS resiliency checks in the EKS MCP Server."""

    def __init__(self, mcp, client_cache):
        """Initialize the EKS resiliency handler.

        Args:
            mcp: The MCP server instance
            client_cache: K8sClientCache instance to share between handlers
        """
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the comprehensive check tool
        self.mcp.tool(name='check_eks_resiliency')(self.check_eks_resiliency)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_resiliency_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load check registry: {e}")
            return {}

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Get all checks flattened into a single dictionary."""
        all_checks = {}
        for category in ['application_checks', 'control_plane_checks', 'data_plane_checks']:
            all_checks.update(self.check_registry.get(category, {}))
        return all_checks

    def _get_check_info(self, check_id: str) -> Dict[str, Any]:
        """Get check information by ID."""
        all_checks = self._get_all_checks()
        return all_checks.get(check_id, {})

    def _get_remediation(self, check_id: str) -> str:
        """Get remediation guidance for a check."""
        check_info = self._get_check_info(check_id)
        return check_info.get('remediation', '')

    def _create_check_result(self, check_id: str, compliant: bool, impacted_resources: List[str], details: str) -> Dict[str, Any]:
        """Create a standardized check result."""
        check_info = self._get_check_info(check_id)
        remediation = self._get_remediation(check_id) if not compliant else ''
        
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'medium'),
            'compliant': compliant,
            'impacted_resources': impacted_resources,
            'details': details,
        }

    def _create_check_error_result(self, check_id: str, error_msg: str) -> Dict[str, Any]:
        """Create an error result for a failed check."""
        check_info = self._get_check_info(check_id)
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'medium'),
            'compliant': False,
            'impacted_resources': [],
            'details': f'Check failed with error: {error_msg}',
        }

    def _create_error_response(self, cluster_name: str, error_msg: str) -> ResiliencyCheckResponse:
        """Create an error response."""
        return ResiliencyCheckResponse(
            isError=True,
            content=[TextContent(type='text', text=f'Failed to connect to cluster {cluster_name}: {error_msg}')],
            check_results=[{
                'check_name': 'Connection Error',
                'compliant': False,
                'impacted_resources': [],
                'details': error_msg,
                'remediation': 'Verify that the cluster exists and is accessible.',
            }],
            overall_compliant=False,
            summary=f'Failed to connect to cluster {cluster_name}: {error_msg}',
        )

    async def check_eks_resiliency(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for resiliency best practices.'
        ),
        namespace: Optional[str] = Field(
            None, description='Optional namespace to limit the check scope.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
    ) -> ResiliencyCheckResponse:
        """Check EKS cluster for resiliency best practices.

        This tool runs a comprehensive set of resiliency checks against your EKS cluster
        to identify potential issues that could impact application availability and
        provides remediation guidance.

        The tool evaluates 28 critical resiliency best practices across three categories:
        - Application Related Checks (A1-A14): Workload resilience and operational practices
        - Control Plane Related Checks (C1-C5): EKS control plane configuration
        - Data Plane Related Checks (D1-D7): Worker node and cluster infrastructure
        """
        try:
            logger.info(f'Starting resiliency check for cluster: {cluster_name}')

            # Get K8s client for the cluster
            try:
                k8s_client = self.client_cache.get_client(cluster_name, region=region)
                logger.info(f'Successfully obtained K8s client for cluster: {cluster_name}')
            except Exception as e:
                logger.error(f'Failed to get K8s client for cluster {cluster_name}: {str(e)}')
                return self._create_error_response(cluster_name, str(e))

            # Initialize shared data once (optimization)
            shared_data = await self._initialize_shared_data(k8s_client, cluster_name, namespace, region)
            if not shared_data:
                return self._create_error_response(cluster_name, "Failed to initialize shared data")

            # Run all checks
            check_results = []
            all_compliant = True
            
            # Get all checks and sort by ID for consistent execution order
            all_checks = self._get_all_checks()
            
            for check_id in sorted(all_checks.keys()):
                try:
                    logger.info(f'Running check {check_id}')
                    result = await self._execute_check(check_id, shared_data, cluster_name, namespace)
                    check_results.append(result)
                    
                    if not result['compliant']:
                        all_compliant = False
                        
                    logger.info(f'Check {check_id} completed: {result["compliant"]}')
                    
                except Exception as e:
                    logger.error(f'Error in check {check_id}: {str(e)}')
                    error_result = self._create_check_error_result(check_id, str(e))
                    check_results.append(error_result)
                    all_compliant = False

            # Generate summary
            passed_count = sum(1 for r in check_results if r['compliant'])
            failed_count = len(check_results) - passed_count
            summary = f'Cluster {cluster_name} resiliency check: {passed_count} checks passed, {failed_count} checks failed'

            # Create detailed response with JSON data in content
            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return ResiliencyCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in resiliency check: {str(e)}')
            return self._create_error_response(cluster_name, str(e))

    async def _initialize_shared_data(self, k8s_client, cluster_name: str, namespace: Optional[str], region: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Initialize shared data once to avoid redundant API calls (optimization)."""
        try:
            shared_data = {}
            
            # Prepare kwargs for filtering
            kwargs = {}
            if namespace:
                kwargs['namespace'] = namespace
            
            # Fetch Pods ONCE (used by A1)
            try:
                pods = k8s_client.list_resources(kind='Pod', api_version='v1', **kwargs)
                shared_data['pods'] = pods.items if hasattr(pods, 'items') else []
                logger.info(f'Fetched {len(shared_data["pods"])} pods once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch pods: {str(e)}')
                shared_data['pods'] = []
            
            # Fetch Deployments ONCE (used by A2, A3, A4, A5, A6)
            try:
                deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', **kwargs)
                shared_data['deployments'] = deployments.items if hasattr(deployments, 'items') else []
                logger.info(f'Fetched {len(shared_data["deployments"])} deployments once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch deployments: {str(e)}')
                shared_data['deployments'] = []
            
            # Fetch StatefulSets ONCE (used by A2, A4, A5, A6)
            try:
                statefulsets = k8s_client.list_resources(kind='StatefulSet', api_version='apps/v1', **kwargs)
                shared_data['statefulsets'] = statefulsets.items if hasattr(statefulsets, 'items') else []
                logger.info(f'Fetched {len(shared_data["statefulsets"])} statefulsets once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch statefulsets: {str(e)}')
                shared_data['statefulsets'] = []
            
            # Fetch DaemonSets ONCE (used by A4, A5)
            try:
                daemonsets = k8s_client.list_resources(kind='DaemonSet', api_version='apps/v1', **kwargs)
                shared_data['daemonsets'] = daemonsets.items if hasattr(daemonsets, 'items') else []
                logger.info(f'Fetched {len(shared_data["daemonsets"])} daemonsets once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch daemonsets: {str(e)}')
                shared_data['daemonsets'] = []
            
            # Fetch PodDisruptionBudgets ONCE (used by A6)
            try:
                pdbs = k8s_client.list_resources(kind='PodDisruptionBudget', api_version='policy/v1', **kwargs)
                shared_data['pdbs'] = pdbs.items if hasattr(pdbs, 'items') else []
                logger.info(f'Fetched {len(shared_data["pdbs"])} PodDisruptionBudgets once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch PodDisruptionBudgets: {str(e)}')
                shared_data['pdbs'] = []
            
            # Fetch HorizontalPodAutoscalers ONCE (used by A8)
            try:
                hpas = k8s_client.list_resources(kind='HorizontalPodAutoscaler', api_version='autoscaling/v2', **kwargs)
                shared_data['hpas'] = hpas.items if hasattr(hpas, 'items') else []
                logger.info(f'Fetched {len(shared_data["hpas"])} HorizontalPodAutoscalers once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch HorizontalPodAutoscalers: {str(e)}')
                shared_data['hpas'] = []
            
            # Fetch kube-system Deployments ONCE (used by A7, A10, A12, A13, A14, C2, D1)
            try:
                kube_system_deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', namespace='kube-system')
                shared_data['kube_system_deployments'] = kube_system_deployments.items if hasattr(kube_system_deployments, 'items') else []
                logger.info(f'Fetched {len(shared_data["kube_system_deployments"])} kube-system deployments once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch kube-system deployments: {str(e)}')
                shared_data['kube_system_deployments'] = []
            
            # Fetch kube-system DaemonSets ONCE (used by A13, A14, C3)
            try:
                kube_system_daemonsets = k8s_client.list_resources(kind='DaemonSet', api_version='apps/v1', namespace='kube-system')
                shared_data['kube_system_daemonsets'] = kube_system_daemonsets.items if hasattr(kube_system_daemonsets, 'items') else []
                logger.info(f'Fetched {len(shared_data["kube_system_daemonsets"])} kube-system daemonsets once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch kube-system daemonsets: {str(e)}')
                shared_data['kube_system_daemonsets'] = []
            
            # Fetch kube-system ConfigMaps ONCE (used by C2, C3)
            try:
                kube_system_configmaps = k8s_client.list_resources(kind='ConfigMap', api_version='v1', namespace='kube-system')
                shared_data['kube_system_configmaps'] = kube_system_configmaps.items if hasattr(kube_system_configmaps, 'items') else []
                logger.info(f'Fetched {len(shared_data["kube_system_configmaps"])} kube-system configmaps once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch kube-system configmaps: {str(e)}')
                shared_data['kube_system_configmaps'] = []
            
            # Initialize AWS EKS client ONCE (used by C1, C2, C4)
            try:
                eks_client = AwsHelper.create_boto3_client('eks', region_name=region)
                shared_data['eks_client'] = eks_client
                
                # Fetch cluster info ONCE (used by C1, C4)
                cluster_info = eks_client.describe_cluster(name=cluster_name)
                shared_data['cluster_info'] = cluster_info['cluster']
                logger.info('Fetched cluster info once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch cluster info: {str(e)}')
                shared_data['eks_client'] = None
                shared_data['cluster_info'] = {}
            
            # Fetch Nodes ONCE (used by D2)
            try:
                nodes = k8s_client.list_resources(kind='Node', api_version='v1')
                shared_data['nodes'] = nodes.items if hasattr(nodes, 'items') else []
                logger.info(f'Fetched {len(shared_data["nodes"])} nodes once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch nodes: {str(e)}')
                shared_data['nodes'] = []
            
            # Fetch addons ONCE (used by D7)
            try:
                if shared_data.get('eks_client'):
                    addons = shared_data['eks_client'].list_addons(clusterName=cluster_name)
                    shared_data['addons'] = addons.get('addons', [])
                    logger.info(f'Fetched {len(shared_data["addons"])} addons once for sharing')
                else:
                    shared_data['addons'] = []
            except Exception as e:
                logger.warning(f'Failed to fetch addons: {str(e)}')
                shared_data['addons'] = []
            
            # Store k8s_client and other context for checks that need it
            shared_data['k8s_client'] = k8s_client
            shared_data['cluster_name'] = cluster_name
            shared_data['namespace'] = namespace
            
            return shared_data
            
        except Exception as e:
            logger.error(f'Failed to initialize shared data: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return None

    async def _execute_check(self, check_id: str, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Execute a single check based on its ID using shared data."""
        
        # Map check IDs to their corresponding methods
        check_methods = {
            'A1': self._check_singleton_pods,
            'A2': self._check_multiple_replicas,
            'A3': self._check_pod_anti_affinity,
            'A4': self._check_liveness_probe,
            'A5': self._check_readiness_probe,
            'A6': self._check_pod_disruption_budget,
            'A7': self._check_metrics_server,
            'A8': self._check_horizontal_pod_autoscaler,
            'A9': self._check_custom_metrics,
            'A10': self._check_vertical_pod_autoscaler,
            'A11': self._check_prestop_hooks,
            'A12': self._check_service_mesh,
            'A13': self._check_monitoring,
            'A14': self._check_centralized_logging,
            'A15': self._check_startup_probe,
            'C1': self._check_c1,
            'C2': self._check_c2,
            'C3': self._check_c3,
            'C4': self._check_c4,
            'C5': self._check_c5,
            'D1': self._check_d1,
            'D2': self._check_d2,
            'D3': self._check_d3,
            'D4': self._check_d4,
            'D5': self._check_d5,
            'D6': self._check_d6,
            'D7': self._check_d7,
            'D8': self._check_d8,
            'D9': self._check_d9,
        }
        
        check_method = check_methods.get(check_id)
        if not check_method:
            return self._create_check_error_result(check_id, f"No implementation found for check {check_id}")
        
        # Execute the check method with shared data
        if check_id.startswith('A'):
            # Application checks use shared data and namespace
            return check_method(shared_data, namespace)
        else:
            # Control plane and data plane checks need cluster_name too
            return check_method(shared_data, cluster_name, namespace)

    # Placeholder check methods - these would contain the actual check logic
    # For now, I'll create simple placeholder implementations that use the JSON configuration
    
    def _check_singleton_pods(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A1: Singleton pods without controller management."""
        singleton_pods = []
        try:
            logger.info(
                f'Starting singleton pods check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared pods (optimization)
            pods = shared_data.get('pods', [])
            logger.info(f'Using {len(pods)} shared pods')

            # Process the pods
            for pod in pods:
                try:
                    pod_dict = pod.to_dict() if hasattr(pod, 'to_dict') else pod

                    # Check if pod has owner references
                    metadata = pod_dict.get('metadata', {})
                    owner_refs = metadata.get('ownerReferences', [])

                    # Make sure we get the namespace from the pod metadata
                    pod_namespace = metadata.get('namespace')
                    if not pod_namespace:
                        logger.warning("Pod missing namespace information, using 'default'")
                        pod_namespace = 'default'

                    name = metadata.get('name', 'unknown')

                    logger.debug(f'Checking pod {pod_namespace}/{name}')

                    if not owner_refs:
                        logger.info(f'Found singleton pod: {pod_namespace}/{name}')
                        singleton_pods.append(f'{pod_namespace}/{name}')
                except Exception as pod_error:
                    logger.error(f'Error processing pod: {str(pod_error)}')

            is_compliant = len(singleton_pods) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(singleton_pods)} singleton pods not managed by controllers {scope_info}'
            
            return self._create_check_result('A1', is_compliant, singleton_pods, detailed_message)

        except Exception as e:
            logger.error(f'Error checking singleton pods: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking singleton pods {scope_info}: {str(e)}'
            return self._create_check_result('A1', False, [], error_message)

    def _check_multiple_replicas(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A2: Deployments and StatefulSets with only one replica."""
        single_replica_workloads = []
        try:
            logger.info(
                f'Starting multiple replicas check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            logger.info(f'Using {len(deployments)} shared deployments')

            # Process deployments
            for deployment in deployments:
                try:
                    deployment_dict = (
                        deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    )

                    # Get deployment metadata
                    metadata = deployment_dict.get('metadata', {})
                    deploy_namespace = metadata.get('namespace', 'default')
                    name = metadata.get('name', 'unknown')

                    # Check replicas
                    spec = deployment_dict.get('spec', {})
                    replicas = spec.get('replicas', 0)

                    logger.debug(
                        f'Checking deployment {deploy_namespace}/{name} with {replicas} replicas'
                    )

                    if replicas == 1:
                        logger.info(f'Found single replica deployment: {deploy_namespace}/{name}')
                        single_replica_workloads.append(f'Deployment {deploy_namespace}/{name}')
                except Exception as deploy_error:
                    logger.error(f'Error processing deployment: {str(deploy_error)}')

            # Use shared statefulsets (optimization)
            statefulsets = shared_data.get('statefulsets', [])
            logger.info(f'Using {len(statefulsets)} shared statefulsets')
            
            try:
                # Process statefulsets
                for statefulset in statefulsets:
                    try:
                        statefulset_dict = (
                            statefulset.to_dict()
                            if hasattr(statefulset, 'to_dict')
                            else statefulset
                        )

                        # Get statefulset metadata
                        metadata = statefulset_dict.get('metadata', {})
                        sts_namespace = metadata.get('namespace', 'default')
                        name = metadata.get('name', 'unknown')

                        # Check replicas
                        spec = statefulset_dict.get('spec', {})
                        replicas = spec.get('replicas', 0)

                        logger.debug(
                            f'Checking statefulset {sts_namespace}/{name} with {replicas} replicas'
                        )

                        if replicas == 1:
                            logger.info(
                                f'Found single replica statefulset: {sts_namespace}/{name}'
                            )
                            single_replica_workloads.append(f'StatefulSet {sts_namespace}/{name}')
                    except Exception as sts_error:
                        logger.error(f'Error processing statefulset: {str(sts_error)}')
            except Exception as sts_list_error:
                logger.warning(f'Error listing statefulsets: {str(sts_list_error)}')

            is_compliant = len(single_replica_workloads) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(single_replica_workloads)} workloads (Deployments/StatefulSets) with only 1 replica {scope_info}'
            
            return self._create_check_result('A2', is_compliant, single_replica_workloads, detailed_message)

        except Exception as e:
            logger.error(f'Error checking multiple replicas: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking workloads for multiple replicas {scope_info}: {str(e)}'
            return self._create_check_result('A2', False, [], error_message)

    def _check_pod_anti_affinity(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A3: Multi-replica deployments without pod anti-affinity."""
        deployments_without_anti_affinity = []
        try:
            logger.info(
                f'Starting pod anti-affinity check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            logger.info(f'Using {len(deployments)} shared deployments')

            # Process the deployments
            for deployment in deployments:
                try:
                    deployment_dict = (
                        deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    )

                    # Get deployment metadata
                    metadata = deployment_dict.get('metadata', {})
                    deploy_namespace = metadata.get('namespace', 'default')
                    name = metadata.get('name', 'unknown')

                    # Check replicas
                    spec = deployment_dict.get('spec', {})
                    replicas = spec.get('replicas', 0)

                    # Skip deployments with only 1 replica
                    if replicas <= 1:
                        logger.debug(
                            f'Skipping deployment {deploy_namespace}/{name} with {replicas} replicas'
                        )
                        continue

                    # Check for pod anti-affinity
                    template_spec = spec.get('template', {}).get('spec', {})
                    affinity = template_spec.get('affinity', {})
                    pod_anti_affinity = affinity.get('podAntiAffinity', None)

                    has_anti_affinity = pod_anti_affinity is not None

                    logger.debug(
                        f'Checking deployment {deploy_namespace}/{name} for pod anti-affinity: {has_anti_affinity}'
                    )

                    if not has_anti_affinity:
                        logger.info(
                            f'Found deployment without pod anti-affinity: {deploy_namespace}/{name}'
                        )
                        deployments_without_anti_affinity.append(f'{deploy_namespace}/{name}')
                except Exception as deploy_error:
                    logger.error(f'Error processing deployment: {str(deploy_error)}')

            is_compliant = len(deployments_without_anti_affinity) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(deployments_without_anti_affinity)} multi-replica deployments without pod anti-affinity {scope_info}'
            
            return self._create_check_result('A3', is_compliant, deployments_without_anti_affinity, detailed_message)

        except Exception as e:
            logger.error(f'Error checking pod anti-affinity: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking deployments for pod anti-affinity {scope_info}: {str(e)}'
            return self._create_check_result('A3', False, [], error_message)

    def _check_liveness_probe(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A4: Deployments without liveness probes."""
        workloads_without_liveness = []
        try:
            logger.info(
                f'Starting liveness probe check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            logger.info(f'Checking {len(deployments)} shared deployments for liveness probes')
            
            try:
                # Process the deployments
                for deployment in deployments:
                    try:
                        deployment_dict = (
                            deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                        )

                        # Get deployment metadata
                        metadata = deployment_dict.get('metadata', {})
                        deploy_namespace = metadata.get('namespace', 'default')
                        name = metadata.get('name', 'unknown')

                        # Check containers for liveness probes
                        template_spec = (
                            deployment_dict.get('spec', {}).get('template', {}).get('spec', {})
                        )
                        containers = template_spec.get('containers', [])

                        has_liveness_probe = True
                        for container in containers:
                            if not container.get('livenessProbe'):
                                has_liveness_probe = False
                                break

                        if not has_liveness_probe:
                            workloads_without_liveness.append(f'Deployment: {deploy_namespace}/{name}')
                    except Exception as deploy_error:
                        logger.error(f'Error processing deployment: {str(deploy_error)}')
            except Exception as e:
                logger.error(f'Error listing deployments: {str(e)}')

            # Use shared statefulsets (optimization)
            statefulsets = shared_data.get('statefulsets', [])
            logger.info(f'Checking {len(statefulsets)} shared statefulsets for liveness probes')
            
            try:
                for statefulset in statefulsets:
                    try:
                        statefulset_dict = (
                            statefulset.to_dict()
                            if hasattr(statefulset, 'to_dict')
                            else statefulset
                        )

                        # Get statefulset metadata
                        metadata = statefulset_dict.get('metadata', {})
                        ss_namespace = metadata.get('namespace', 'default')
                        name = metadata.get('name', 'unknown')

                        # Check containers for liveness probes
                        template_spec = (
                            statefulset_dict.get('spec', {}).get('template', {}).get('spec', {})
                        )
                        containers = template_spec.get('containers', [])

                        has_liveness_probe = True
                        for container in containers:
                            if not container.get('livenessProbe'):
                                has_liveness_probe = False
                                break

                        if not has_liveness_probe:
                            workloads_without_liveness.append(f'StatefulSet: {ss_namespace}/{name}')
                    except Exception as ss_error:
                        logger.error(f'Error processing statefulset: {str(ss_error)}')
            except Exception as e:
                logger.error(f'Error listing statefulsets: {str(e)}')

            # Use shared daemonsets (optimization)
            daemonsets = shared_data.get('daemonsets', [])
            logger.info(f'Checking {len(daemonsets)} shared daemonsets for liveness probes')
            
            try:
                for daemonset in daemonsets:
                    try:
                        daemonset_dict = (
                            daemonset.to_dict() if hasattr(daemonset, 'to_dict') else daemonset
                        )

                        # Get daemonset metadata
                        metadata = daemonset_dict.get('metadata', {})
                        ds_namespace = metadata.get('namespace', 'default')
                        name = metadata.get('name', 'unknown')

                        # Check containers for liveness probes
                        template_spec = (
                            daemonset_dict.get('spec', {}).get('template', {}).get('spec', {})
                        )
                        containers = template_spec.get('containers', [])

                        has_liveness_probe = True
                        for container in containers:
                            if not container.get('livenessProbe'):
                                has_liveness_probe = False
                                break

                        if not has_liveness_probe:
                            workloads_without_liveness.append(f'DaemonSet: {ds_namespace}/{name}')
                    except Exception as ds_error:
                        logger.error(f'Error processing daemonset: {str(ds_error)}')
            except Exception as e:
                logger.error(f'Error listing daemonsets: {str(e)}')

            is_compliant = len(workloads_without_liveness) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(workloads_without_liveness)} workloads without liveness probes {scope_info}'
            
            return self._create_check_result('A4', is_compliant, workloads_without_liveness, detailed_message)

        except Exception as e:
            logger.error(f'Error checking liveness probes: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking workloads for liveness probes {scope_info}: {str(e)}'
            return self._create_check_result('A4', False, [], error_message)

    def _check_readiness_probe(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A5: Deployments without readiness probes."""
        workloads_without_readiness = []
        try:
            logger.info(
                f'Starting readiness probe check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared workloads (optimization)
            workload_types = {
                'Deployment': shared_data.get('deployments', []),
                'StatefulSet': shared_data.get('statefulsets', []),
                'DaemonSet': shared_data.get('daemonsets', [])
            }
            
            # Check Deployments, StatefulSets, and DaemonSets
            for workload_type, workloads in workload_types.items():
                try:
                    logger.info(f'Checking {len(workloads)} shared {workload_type}s for readiness probes')
                    
                    for workload in workloads:
                        try:
                            workload_dict = (
                                workload.to_dict() if hasattr(workload, 'to_dict') else workload
                            )

                            # Get workload metadata
                            metadata = workload_dict.get('metadata', {})
                            workload_namespace = metadata.get('namespace', 'default')
                            name = metadata.get('name', 'unknown')

                            # Check containers for readiness probes
                            template_spec = (
                                workload_dict.get('spec', {}).get('template', {}).get('spec', {})
                            )
                            containers = template_spec.get('containers', [])

                            has_readiness_probe = True
                            for container in containers:
                                if not container.get('readinessProbe'):
                                    has_readiness_probe = False
                                    break

                            if not has_readiness_probe:
                                workloads_without_readiness.append(f'{workload_type}: {workload_namespace}/{name}')
                        except Exception as workload_error:
                            logger.error(f'Error processing {workload_type}: {str(workload_error)}')
                except Exception as e:
                    logger.error(f'Error listing {workload_type}s: {str(e)}')

            is_compliant = len(workloads_without_readiness) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(workloads_without_readiness)} workloads without readiness probes {scope_info}'
            
            return self._create_check_result('A5', is_compliant, workloads_without_readiness, detailed_message)

        except Exception as e:
            logger.error(f'Error checking readiness probes: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking workloads for readiness probes {scope_info}: {str(e)}'
            return self._create_check_result('A5', False, [], error_message)

    def _check_pod_disruption_budget(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A6: Critical workloads without Pod Disruption Budgets."""
        workloads_without_pdb = []
        try:
            logger.info(
                f'Starting pod disruption budget check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared PDBs (optimization)
            pdbs = shared_data.get('pdbs', [])
            logger.info(f'Using {len(pdbs)} shared PodDisruptionBudgets')
            
            pdb_selectors = []
            for pdb in pdbs:
                try:
                    pdb_dict = pdb.to_dict() if hasattr(pdb, 'to_dict') else pdb
                    spec = pdb_dict.get('spec', {})
                    selector = spec.get('selector', {})
                    if selector:
                        pdb_selectors.append(selector)
                except Exception as e:
                    logger.error(f'Error processing PDB: {str(e)}')

            # Use shared workloads (optimization)
            workload_types = {
                'Deployment': shared_data.get('deployments', []),
                'StatefulSet': shared_data.get('statefulsets', [])
            }
            
            # Check critical workloads (multi-replica Deployments and all StatefulSets)
            for workload_type, workloads in workload_types.items():
                try:
                    logger.info(f'Checking {len(workloads)} shared {workload_type}s for PDBs')
                    
                    for workload in workloads:
                        try:
                            workload_dict = (
                                workload.to_dict() if hasattr(workload, 'to_dict') else workload
                            )

                            metadata = workload_dict.get('metadata', {})
                            workload_namespace = metadata.get('namespace', 'default')
                            name = metadata.get('name', 'unknown')

                            # For Deployments, only check multi-replica ones
                            if workload_type == 'Deployment':
                                spec = workload_dict.get('spec', {})
                                replicas = spec.get('replicas', 0)
                                if replicas <= 1:
                                    continue

                            # PDB selectors match pod labels, which come from spec.selector.matchLabels
                            # (not metadata.labels which are the workload's own labels)
                            pod_labels = workload_dict.get('spec', {}).get('selector', {}).get('matchLabels', {})

                            # Check if workload is covered by any PDB
                            has_pdb = False
                            for pdb_selector in pdb_selectors:
                                match_labels = pdb_selector.get('matchLabels', {})
                                if match_labels and all(pod_labels.get(k) == v for k, v in match_labels.items()):
                                    has_pdb = True
                                    break

                            if not has_pdb:
                                workloads_without_pdb.append(f'{workload_type}: {workload_namespace}/{name}')
                        except Exception as workload_error:
                            logger.error(f'Error processing {workload_type}: {str(workload_error)}')
                except Exception as e:
                    logger.error(f'Error listing {workload_type}s: {str(e)}')

            is_compliant = len(workloads_without_pdb) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(workloads_without_pdb)} critical workloads without Pod Disruption Budgets {scope_info}'
            
            return self._create_check_result('A6', is_compliant, workloads_without_pdb, detailed_message)

        except Exception as e:
            logger.error(f'Error checking pod disruption budgets: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking pod disruption budgets {scope_info}: {str(e)}'
            return self._create_check_result('A6', False, [], error_message)

    def _check_metrics_server(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A7: Kubernetes Metrics Server."""
        try:
            logger.info('Starting metrics server check')

            # Check if metrics API is available
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/metrics.k8s.io/v1beta1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                metrics_api_available = True
            except Exception as e:
                logger.info(f'Metrics API not available: {str(e)}')
                metrics_api_available = False

            # Use shared kube-system deployments (optimization)
            kube_system_deployments = shared_data.get('kube_system_deployments', [])
            logger.info(f'Using {len(kube_system_deployments)} shared kube-system deployments')
            
            try:
                metrics_server_found = False
                for deployment in kube_system_deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'metrics-server' in name.lower():
                        metrics_server_found = True
                        break
            except Exception as e:
                logger.error(f'Error checking metrics-server deployment: {str(e)}')
                metrics_server_found = False

            is_compliant = metrics_api_available and metrics_server_found
            
            if is_compliant:
                details = 'Metrics server is running and metrics API is accessible'
                impacted_resources = []
            else:
                details = 'Metrics server is not properly configured or not accessible'
                impacted_resources = ['metrics-server']
            
            return self._create_check_result('A7', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking metrics server: {str(e)}')
            error_message = f'Error checking metrics server: {str(e)}'
            return self._create_check_result('A7', False, [], error_message)

    def _check_horizontal_pod_autoscaler(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A8: Horizontal Pod Autoscaler (HPA)."""
        workloads_without_hpa = []
        try:
            logger.info(
                f'Starting HPA check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared HPAs (optimization)
            hpas = shared_data.get('hpas', [])
            logger.info(f'Using {len(hpas)} shared HPAs')
            
            try:
                hpa_targets = set()
                for hpa in hpas:
                    try:
                        hpa_dict = hpa.to_dict() if hasattr(hpa, 'to_dict') else hpa
                        spec = hpa_dict.get('spec', {})
                        scale_target_ref = spec.get('scaleTargetRef', {})
                        target_name = scale_target_ref.get('name')
                        target_kind = scale_target_ref.get('kind')
                        if target_name and target_kind:
                            hpa_targets.add(f'{target_kind}:{target_name}')
                    except Exception as e:
                        logger.error(f'Error processing HPA: {str(e)}')
            except Exception as e:
                logger.info(f'No HPAs found or error accessing HPAs: {str(e)}')
                hpa_targets = set()

            # Use shared workloads (optimization)
            workload_types = {
                'Deployment': shared_data.get('deployments', []),
                'StatefulSet': shared_data.get('statefulsets', [])
            }
            
            # Check multi-replica Deployments and StatefulSets
            for workload_type, workloads in workload_types.items():
                try:
                    logger.info(f'Checking {len(workloads)} shared {workload_type}s for HPA')
                    
                    for workload in workloads:
                        try:
                            workload_dict = (
                                workload.to_dict() if hasattr(workload, 'to_dict') else workload
                            )

                            metadata = workload_dict.get('metadata', {})
                            workload_namespace = metadata.get('namespace', 'default')
                            name = metadata.get('name', 'unknown')

                            # Skip system namespaces
                            if workload_namespace in ['kube-system', 'kube-public', 'kube-node-lease']:
                                continue

                            # Only check multi-replica workloads
                            spec = workload_dict.get('spec', {})
                            replicas = spec.get('replicas', 0)
                            if replicas <= 1:
                                continue

                            # Check if workload has HPA
                            target_key = f'{workload_type}:{name}'
                            if target_key not in hpa_targets:
                                workloads_without_hpa.append(f'{workload_type}: {workload_namespace}/{name}')
                        except Exception as workload_error:
                            logger.error(f'Error processing {workload_type}: {str(workload_error)}')
                except Exception as e:
                    logger.error(f'Error listing {workload_type}s: {str(e)}')

            is_compliant = len(workloads_without_hpa) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(workloads_without_hpa)} multi-replica workloads without HPA {scope_info}'
            
            return self._create_check_result('A8', is_compliant, workloads_without_hpa, detailed_message)

        except Exception as e:
            logger.error(f'Error checking horizontal pod autoscaler: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking HPA {scope_info}: {str(e)}'
            return self._create_check_result('A8', False, [], error_message)

    def _check_custom_metrics(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A9: Custom metrics scaling."""
        try:
            logger.info('Starting custom metrics check')

            custom_metrics_available = False
            external_metrics_available = False
            
            # Check for custom metrics API
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/custom.metrics.k8s.io/v1beta1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                custom_metrics_available = True
            except Exception:
                pass

            # Check for external metrics API
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/external.metrics.k8s.io/v1beta1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                external_metrics_available = True
            except Exception:
                pass

            is_compliant = custom_metrics_available or external_metrics_available
            
            if is_compliant:
                details = 'Custom metrics scaling infrastructure is available'
                impacted_resources = []
            else:
                details = 'No custom metrics scaling infrastructure found'
                impacted_resources = ['custom-metrics-api']
            
            return self._create_check_result('A9', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking custom metrics: {str(e)}')
            error_message = f'Error checking custom metrics: {str(e)}'
            return self._create_check_result('A9', False, [], error_message)

    def _check_vertical_pod_autoscaler(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A10: Vertical Pod Autoscaler (VPA)."""
        try:
            logger.info('Starting VPA check')

            # Check for VPA CRD
            vpa_crd_available = False
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/autoscaling.k8s.io/v1/verticalpodautoscalers',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                vpa_crd_available = True
            except Exception:
                pass

            # Use shared kube-system deployments (optimization)
            kube_system_deployments = shared_data.get('kube_system_deployments', [])
            
            # Check for VPA controller components
            vpa_components_found = []
            try:
                for deployment in kube_system_deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if any(component in name.lower() for component in ['vpa-recommender', 'vpa-updater', 'vpa-admission-controller']):
                        vpa_components_found.append(name)
            except Exception as e:
                logger.error(f'Error checking VPA components: {str(e)}')

            is_compliant = vpa_crd_available and len(vpa_components_found) > 0
            
            if is_compliant:
                details = f'VPA is available with components: {", ".join(vpa_components_found)}'
                impacted_resources = []
            else:
                details = 'VPA is not installed or not properly configured'
                impacted_resources = ['vpa-controller']
            
            return self._create_check_result('A10', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking VPA: {str(e)}')
            error_message = f'Error checking VPA: {str(e)}'
            return self._create_check_result('A10', False, [], error_message)

    def _check_prestop_hooks(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A11: PreStop hooks for graceful termination."""
        workloads_without_prestop = []
        try:
            logger.info(
                f'Starting preStop hooks check, namespace: {namespace if namespace else "all"}'
            )

            # Use shared workloads (optimization)
            workload_types = {
                'Deployment': shared_data.get('deployments', []),
                'StatefulSet': shared_data.get('statefulsets', [])
            }
            
            # Check Deployments and StatefulSets (exclude DaemonSets as they're system services)
            for workload_type, workloads in workload_types.items():
                try:
                    logger.info(f'Checking {len(workloads)} shared {workload_type}s for preStop hooks')
                    
                    for workload in workloads:
                        try:
                            workload_dict = (
                                workload.to_dict() if hasattr(workload, 'to_dict') else workload
                            )

                            metadata = workload_dict.get('metadata', {})
                            workload_namespace = metadata.get('namespace', 'default')
                            name = metadata.get('name', 'unknown')

                            # Check containers for preStop hooks
                            template_spec = (
                                workload_dict.get('spec', {}).get('template', {}).get('spec', {})
                            )
                            containers = template_spec.get('containers', [])

                            has_prestop_hook = False
                            for container in containers:
                                lifecycle = container.get('lifecycle', {})
                                if lifecycle.get('preStop'):
                                    has_prestop_hook = True
                                    break

                            if not has_prestop_hook:
                                workloads_without_prestop.append(f'{workload_type}: {workload_namespace}/{name}')
                        except Exception as workload_error:
                            logger.error(f'Error processing {workload_type}: {str(workload_error)}')
                except Exception as e:
                    logger.error(f'Error listing {workload_type}s: {str(e)}')

            is_compliant = len(workloads_without_prestop) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(workloads_without_prestop)} workloads without preStop hooks {scope_info}'
            
            return self._create_check_result('A11', is_compliant, workloads_without_prestop, detailed_message)

        except Exception as e:
            logger.error(f'Error checking preStop hooks: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking preStop hooks {scope_info}: {str(e)}'
            return self._create_check_result('A11', False, [], error_message)

    def _check_service_mesh(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A12: Service mesh usage."""
        try:
            logger.info('Starting service mesh check')

            service_mesh_found = []
            
            # Check for Istio
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/networking.istio.io/v1beta1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                service_mesh_found.append('Istio')
            except Exception:
                pass

            # Check for Linkerd
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/linkerd.io/v1alpha2',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                service_mesh_found.append('Linkerd')
            except Exception:
                pass

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            
            # Check for Consul Connect
            try:
                for deployment in deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'consul' in name.lower():
                        service_mesh_found.append('Consul')
                        break
            except Exception:
                pass

            is_compliant = len(service_mesh_found) > 0
            
            if is_compliant:
                details = f'Service mesh detected: {", ".join(service_mesh_found)}'
                impacted_resources = service_mesh_found
            else:
                details = 'No service mesh implementation found'
                impacted_resources = []
            
            return self._create_check_result('A12', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking service mesh: {str(e)}')
            error_message = f'Error checking service mesh: {str(e)}'
            return self._create_check_result('A12', False, [], error_message)

    def _check_monitoring(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A13: Application monitoring."""
        try:
            logger.info('Starting monitoring check')

            monitoring_found = []
            k8s_client = shared_data.get('k8s_client')
            
            # Check for Prometheus
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/monitoring.coreos.com/v1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                monitoring_found.append('Prometheus Operator')
            except Exception:
                pass

            # Check for CloudWatch Container Insights
            try:
                daemonsets_response = k8s_client.list_resources(
                    kind='DaemonSet', api_version='apps/v1', namespace='amazon-cloudwatch'
                )
                
                for ds in daemonsets_response.items:
                    ds_dict = ds.to_dict() if hasattr(ds, 'to_dict') else ds
                    metadata = ds_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'cloudwatch' in name.lower():
                        monitoring_found.append('CloudWatch Container Insights')
                        break
            except Exception:
                pass

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            
            # Check for other monitoring solutions
            try:
                for deployment in deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if any(monitor in name.lower() for monitor in ['datadog', 'newrelic', 'dynatrace']):
                        monitoring_found.append(name)
            except Exception:
                pass

            is_compliant = len(monitoring_found) > 0
            
            if is_compliant:
                details = f'Monitoring solution detected: {", ".join(monitoring_found)}'
                impacted_resources = monitoring_found
            else:
                details = 'No monitoring solution found'
                impacted_resources = []
            
            return self._create_check_result('A13', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking monitoring: {str(e)}')
            error_message = f'Error checking monitoring: {str(e)}'
            return self._create_check_result('A13', False, [], error_message)

    def _check_centralized_logging(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A14: Centralized logging."""
        try:
            logger.info('Starting centralized logging check')

            logging_found = []
            
            # Check for Elasticsearch/OpenSearch
            try:
                shared_data['k8s_client'].api_client.call_api(
                    '/apis/elasticsearch.k8s.elastic.co/v1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                logging_found.append('Elasticsearch')
            except Exception:
                pass

            # Use shared daemonsets (optimization)
            daemonsets = shared_data.get('daemonsets', [])
            
            # Check for Fluentd/Fluent Bit
            try:
                for ds in daemonsets:
                    ds_dict = ds.to_dict() if hasattr(ds, 'to_dict') else ds
                    metadata = ds_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if any(log_tool in name.lower() for log_tool in ['fluentd', 'fluent-bit', 'filebeat']):
                        logging_found.append(name)
            except Exception:
                pass

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])
            
            # Check for Loki
            try:
                for deployment in deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'loki' in name.lower():
                        logging_found.append('Loki')
                        break
            except Exception:
                pass

            is_compliant = len(logging_found) > 0
            
            if is_compliant:
                details = f'Centralized logging detected: {", ".join(logging_found)}'
                impacted_resources = logging_found
            else:
                details = 'No centralized logging solution found'
                impacted_resources = []
            
            return self._create_check_result('A14', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking centralized logging: {str(e)}')
            error_message = f'Error checking centralized logging: {str(e)}'
            return self._create_check_result('A14', False, [], error_message)    

    def _check_startup_probe(self, shared_data: Dict[str, Any], namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check A15: Workloads with liveness probes but no startup probes.

        Startup probes prevent liveness probes from running until the app has
        fully started. Without them, slow-starting apps get killed by liveness
        probes before they're ready — a common production crash-loop cause.
        """
        workloads_at_risk = []
        try:
            logger.info(
                f'Starting startup probe check, namespace: {namespace if namespace else "all"}'
            )

            # Reuse shared workloads (same data as A4/A5)
            workload_types = {
                'Deployment': shared_data.get('deployments', []),
                'StatefulSet': shared_data.get('statefulsets', []),
            }

            for workload_type, workloads in workload_types.items():
                for workload in workloads:
                    try:
                        workload_dict = (
                            workload.to_dict() if hasattr(workload, 'to_dict') else workload
                        )
                        metadata = workload_dict.get('metadata', {})
                        wl_namespace = metadata.get('namespace', 'default')
                        name = metadata.get('name', 'unknown')

                        template_spec = (
                            workload_dict.get('spec', {}).get('template', {}).get('spec', {})
                        )
                        containers = template_spec.get('containers', [])

                        for container in containers:
                            has_liveness = bool(container.get('livenessProbe'))
                            has_startup = bool(container.get('startupProbe'))

                            # Only flag if liveness exists but startup doesn't
                            if has_liveness and not has_startup:
                                workloads_at_risk.append(
                                    f'{workload_type}: {wl_namespace}/{name}'
                                )
                                break  # one container is enough to flag the workload
                    except Exception as e:
                        logger.error(f'Error processing {workload_type}: {str(e)}')

            is_compliant = len(workloads_at_risk) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = (
                f'Found {len(workloads_at_risk)} workloads with liveness probes '
                f'but no startup probes {scope_info}'
            )

            return self._create_check_result('A15', is_compliant, workloads_at_risk, detailed_message)

        except Exception as e:
            logger.error(f'Error checking startup probes: {str(e)}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking startup probes {scope_info}: {str(e)}'
            return self._create_check_result('A15', False, [], error_message)
    def _check_c1(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check C1: Monitor Control Plane Logs."""
        try:
            logger.info(f'Starting control plane logs check for cluster: {cluster_name}')

            # Use shared cluster info (optimization)
            cluster_data = shared_data.get('cluster_info', {})
            
            # Check logging configuration
            # clusterLogging is a list of {types: [...], enabled: bool}
            logging_config = cluster_data.get('logging', {})
            cluster_logging = logging_config.get('clusterLogging', [])
            
            enabled_log_types = []
            for log_config in cluster_logging:
                if isinstance(log_config, dict) and log_config.get('enabled', False):
                    enabled_log_types.extend(log_config.get('types', []))
            
            # Check if 'api' logging is enabled (minimum requirement)
            api_logging_enabled = 'api' in enabled_log_types
            
            is_compliant = api_logging_enabled
            
            if is_compliant:
                details = f'Control plane logging is enabled for: {", ".join(enabled_log_types)}'
                impacted_resources = []
            else:
                details = 'Control plane logging is not enabled'
                impacted_resources = [cluster_name]
            
            return self._create_check_result('C1', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking control plane logs: {str(e)}')
            error_message = f'Error checking control plane logs: {str(e)}'
            return self._create_check_result('C1', False, [], error_message)

    def _check_c2(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check C2: Cluster Authentication."""
        try:
            logger.info(f'Starting cluster authentication check for cluster: {cluster_name}')

            # Use shared EKS client (optimization)
            eks_client = shared_data.get('eks_client')

            auth_methods_found = []
            
            # Check for EKS Access Entries (modern method)
            if eks_client:
                try:
                    access_entries = eks_client.list_access_entries(clusterName=cluster_name)
                    if access_entries.get('accessEntries'):
                        auth_methods_found.append('EKS Access Entries')
                except Exception as e:
                    logger.info(f'No EKS Access Entries found: {str(e)}')

            # Use shared kube-system configmaps (optimization)
            kube_system_configmaps = shared_data.get('kube_system_configmaps', [])
            
            # Check for aws-auth ConfigMap (legacy method)
            try:
                for cm in kube_system_configmaps:
                    cm_dict = cm.to_dict() if hasattr(cm, 'to_dict') else cm
                    metadata = cm_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if name == 'aws-auth':
                        auth_methods_found.append('aws-auth ConfigMap')
                        break
            except Exception as e:
                logger.info(f'Error checking aws-auth ConfigMap: {str(e)}')

            is_compliant = len(auth_methods_found) > 0
            
            if is_compliant:
                details = f'Cluster authentication configured via: {", ".join(auth_methods_found)}'
                impacted_resources = []
            else:
                details = 'No cluster authentication method found'
                impacted_resources = [cluster_name]
            
            return self._create_check_result('C2', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking cluster authentication: {str(e)}')
            error_message = f'Error checking cluster authentication: {str(e)}'
            return self._create_check_result('C2', False, [], error_message)

    def _check_c3(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check C3: Running large clusters."""
        try:
            logger.info(f'Starting large clusters check for cluster: {cluster_name}')

            k8s_client = shared_data.get('k8s_client')
            # Count total services in the cluster
            services_response = k8s_client.list_resources(kind='Service', api_version='v1')
            service_count = len(services_response.items) if hasattr(services_response, 'items') else 0
            
            # If less than 1000 services, cluster is not considered large
            if service_count < 1000:
                details = f'Cluster has {service_count} services (not a large cluster)'
                return self._create_check_result('C3', True, [], details)

            # For large clusters, check optimizations
            optimizations_missing = []
            
            # Use shared kube-system configmaps (optimization)
            kube_system_configmaps = shared_data.get('kube_system_configmaps', [])
            
            # Check kube-proxy mode
            try:
                ipvs_mode_enabled = False
                for cm in kube_system_configmaps:
                    cm_dict = cm.to_dict() if hasattr(cm, 'to_dict') else cm
                    metadata = cm_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'kube-proxy' in name.lower():
                        data = cm_dict.get('data', {})
                        config_content = data.get('config.conf', '')
                        if 'mode: "ipvs"' in config_content or 'mode: ipvs' in config_content:
                            ipvs_mode_enabled = True
                            break
                
                if not ipvs_mode_enabled:
                    optimizations_missing.append('IPVS mode for kube-proxy')
            except Exception as e:
                logger.error(f'Error checking kube-proxy mode: {str(e)}')

            # Use shared kube-system daemonsets (optimization)
            kube_system_daemonsets = shared_data.get('kube_system_daemonsets', [])
            
            # Check AWS VPC CNI IP caching
            try:
                ip_caching_configured = False
                for ds in kube_system_daemonsets:
                    ds_dict = ds.to_dict() if hasattr(ds, 'to_dict') else ds
                    metadata = ds_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if name == 'aws-node':
                        spec = ds_dict.get('spec', {})
                        template = spec.get('template', {})
                        template_spec = template.get('spec', {})
                        containers = template_spec.get('containers', [])
                        
                        for container in containers:
                            env_vars = container.get('env', [])
                            for env_var in env_vars:
                                if env_var.get('name') == 'WARM_IP_TARGET':
                                    ip_caching_configured = True
                                    break
                        break
                
                if not ip_caching_configured:
                    optimizations_missing.append('AWS VPC CNI IP caching')
            except Exception as e:
                logger.error(f'Error checking VPC CNI configuration: {str(e)}')

            is_compliant = len(optimizations_missing) == 0
            
            if is_compliant:
                details = f'Large cluster ({service_count} services) has proper optimizations configured'
                impacted_resources = []
            else:
                details = f'Large cluster ({service_count} services) missing optimizations: {", ".join(optimizations_missing)}'
                impacted_resources = optimizations_missing
            
            return self._create_check_result('C3', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking large clusters: {str(e)}')
            error_message = f'Error checking large clusters: {str(e)}'
            return self._create_check_result('C3', False, [], error_message)

    def _check_c4(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check C4: EKS Control Plane Endpoint Access Control."""
        try:
            logger.info(f'Starting endpoint access control check for cluster: {cluster_name}')

            # Use shared cluster info (optimization)
            cluster_data = shared_data.get('cluster_info', {})
            
            # Check endpoint configuration
            resources_vpc_config = cluster_data.get('resourcesVpcConfig', {})
            endpoint_config_public_access = resources_vpc_config.get('endpointConfigPublicAccess', True)
            public_access_cidrs = resources_vpc_config.get('publicAccessCidrs', [])
            
            # Check if public access is unrestricted (0.0.0.0/0)
            unrestricted_access = endpoint_config_public_access and '0.0.0.0/0' in public_access_cidrs
            
            is_compliant = not unrestricted_access
            
            if is_compliant:
                if not endpoint_config_public_access:
                    details = 'API server endpoint is private only'
                else:
                    details = f'API server endpoint public access is restricted to: {", ".join(public_access_cidrs)}'
                impacted_resources = []
            else:
                details = 'API server endpoint has unrestricted public access (0.0.0.0/0)'
                impacted_resources = [cluster_name]
            
            return self._create_check_result('C4', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking endpoint access control: {str(e)}')
            error_message = f'Error checking endpoint access control: {str(e)}'
            return self._create_check_result('C4', False, [], error_message)

    def _check_c5(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check C5: Avoid catch-all admission webhooks."""
        try:
            logger.info('Starting admission webhooks check')

            k8s_client = shared_data.get('k8s_client')
            catch_all_webhooks = []
            
            # Check MutatingAdmissionWebhooks
            try:
                mutating_webhooks_response = k8s_client.list_resources(
                    kind='MutatingAdmissionWebhook', api_version='admissionregistration.k8s.io/v1'
                )
                
                for webhook in mutating_webhooks_response.items:
                    webhook_dict = webhook.to_dict() if hasattr(webhook, 'to_dict') else webhook
                    metadata = webhook_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    
                    webhooks_list = webhook_dict.get('webhooks', [])
                    for wh in webhooks_list:
                        rules = wh.get('rules', [])
                        for rule in rules:
                            api_groups = rule.get('apiGroups', [])
                            api_versions = rule.get('apiVersions', [])
                            resources = rule.get('resources', [])
                            
                            if '*' in api_groups or '*' in api_versions or '*' in resources:
                                catch_all_webhooks.append(f'MutatingAdmissionWebhook: {name}')
                                break
            except Exception as e:
                logger.info(f'Error checking mutating webhooks: {str(e)}')

            # Check ValidatingAdmissionWebhooks
            try:
                validating_webhooks_response = k8s_client.list_resources(
                    kind='ValidatingAdmissionWebhook', api_version='admissionregistration.k8s.io/v1'
                )
                
                for webhook in validating_webhooks_response.items:
                    webhook_dict = webhook.to_dict() if hasattr(webhook, 'to_dict') else webhook
                    metadata = webhook_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    
                    webhooks_list = webhook_dict.get('webhooks', [])
                    for wh in webhooks_list:
                        rules = wh.get('rules', [])
                        for rule in rules:
                            api_groups = rule.get('apiGroups', [])
                            api_versions = rule.get('apiVersions', [])
                            resources = rule.get('resources', [])
                            
                            if '*' in api_groups or '*' in api_versions or '*' in resources:
                                catch_all_webhooks.append(f'ValidatingAdmissionWebhook: {name}')
                                break
            except Exception as e:
                logger.info(f'Error checking validating webhooks: {str(e)}')

            is_compliant = len(catch_all_webhooks) == 0
            
            if is_compliant:
                details = 'No catch-all admission webhooks found'
                impacted_resources = []
            else:
                details = f'Found {len(catch_all_webhooks)} catch-all admission webhooks'
                impacted_resources = catch_all_webhooks
            
            return self._create_check_result('C5', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking admission webhooks: {str(e)}')
            error_message = f'Error checking admission webhooks: {str(e)}'
            return self._create_check_result('C5', False, [], error_message)   
    def _check_d1(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D1: Use Kubernetes Cluster Autoscaler or Karpenter."""
        try:
            logger.info('Starting node autoscaling check')

            autoscaling_solutions = []

            # Use shared kube-system deployments (optimization)
            kube_system_deployments = shared_data.get('kube_system_deployments', [])

            # Check for Cluster Autoscaler
            try:
                for deployment in kube_system_deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'cluster-autoscaler' in name.lower():
                        autoscaling_solutions.append(f'Cluster Autoscaler: {name}')
                        break
            except Exception as e:
                logger.info(f'Error checking cluster autoscaler: {str(e)}')

            # Check for Karpenter — detect by controller deployment name, which is
            # version-independent (no reliance on the karpenter.sh API version) and
            # namespace-independent (Karpenter is commonly installed in either the
            # 'karpenter' or 'kube-system' namespace).
            try:
                karpenter_found = self._detect_karpenter_deployment(shared_data)
                if karpenter_found:
                    autoscaling_solutions.append(f'Karpenter: {karpenter_found}')
            except Exception as e:
                logger.info(f'Karpenter not found: {str(e)}')

            is_compliant = len(autoscaling_solutions) > 0
            
            if is_compliant:
                details = f'Node autoscaling available: {", ".join(autoscaling_solutions)}'
                impacted_resources = autoscaling_solutions
            else:
                details = 'No node autoscaling solution found'
                impacted_resources = []
            
            return self._create_check_result('D1', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking node autoscaling: {str(e)}')
            error_message = f'Error checking node autoscaling: {str(e)}'
            return self._create_check_result('D1', False, [], error_message)

    def _detect_karpenter_deployment(self, shared_data: Dict[str, Any]) -> Optional[str]:
        """Return 'namespace/name' of the Karpenter controller deployment, or None.

        Detection is by deployment name (any deployment whose name contains
        'karpenter'), which works regardless of Karpenter API version (v1beta1
        vs v1) and regardless of install namespace ('karpenter' or 'kube-system').

        Reuses already-fetched deployment collections in shared_data when
        possible; only issues a cluster-wide Deployment list as a fallback.
        """
        def _scan(deployments) -> Optional[str]:
            for deployment in deployments or []:
                d = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                meta = d.get('metadata', {})
                name = meta.get('name', '')
                if 'karpenter' in name.lower():
                    ns = meta.get('namespace', '')
                    return f'{ns}/{name}' if ns else name
            return None

        # 1. Check cached kube-system deployments (common install location).
        hit = _scan(shared_data.get('kube_system_deployments', []))
        if hit:
            return hit

        # 2. Check the general deployments cache (may be namespace-scoped).
        hit = _scan(shared_data.get('deployments', []))
        if hit:
            return hit

        # 3. Fallback: list deployments cluster-wide (covers a dedicated
        #    'karpenter' namespace not represented in the caches above).
        k8s_client = shared_data.get('k8s_client')
        if k8s_client is not None:
            try:
                all_deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1')
                items = all_deployments.items if hasattr(all_deployments, 'items') else []
                return _scan(items)
            except Exception as e:
                logger.info(f'Cluster-wide Karpenter deployment scan failed: {str(e)}')

        return None

    def _check_d2(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D2: Worker nodes spread across multiple AZs."""
        try:
            logger.info(f'Checking multi-AZ node distribution for cluster: {cluster_name}')
            
            # Use shared nodes (optimization)
            nodes_list = shared_data.get('nodes', [])
            
            if not nodes_list:
                details = {
                    'cluster_name': cluster_name,
                    'error': 'No nodes found in cluster',
                    'compliance_status': 'non-compliant',
                    'risk_level': 'high'
                }
                return self._create_check_result('D2', False, [], str(details))
            
            # Extract AZ information from nodes
            az_distribution = {}
            total_nodes = 0
            impacted_resources = []
            
            for node in nodes_list:
                try:
                    node_dict = node.to_dict() if hasattr(node, 'to_dict') else node
                    metadata = node_dict.get('metadata', {})
                    labels = metadata.get('labels', {})
                    node_name = metadata.get('name', 'unknown')
                    
                    # Get AZ from node labels
                    az = labels.get('topology.kubernetes.io/zone') or labels.get('failure-domain.beta.kubernetes.io/zone')
                    
                    if az:
                        az_distribution[az] = az_distribution.get(az, 0) + 1
                        total_nodes += 1
                    else:
                        logger.warning(f'Node {node_name} missing AZ label')
                        impacted_resources.append(f'Node: {node_name} (missing AZ label)')
                        
                except Exception as node_error:
                    logger.error(f'Error processing node: {str(node_error)}')
            
            logger.info(f'Node distribution across AZs: {az_distribution}')
            
            # Analyze distribution
            if total_nodes == 0:
                details = {
                    'cluster_name': cluster_name,
                    'error': 'No nodes with AZ labels found',
                    'compliance_status': 'non-compliant',
                    'risk_level': 'high'
                }
                return self._create_check_result('D2', False, impacted_resources, str(details))
            
            # Check if nodes are distributed across multiple AZs
            num_azs = len(az_distribution)
            issues = []
            
            if num_azs < 2:
                is_compliant = False
                issues.append(f'Nodes are only in {num_azs} availability zone(s). Minimum 2 AZs recommended for high availability.')
                risk_level = 'high'
                for az in az_distribution.keys():
                    impacted_resources.append(f'All nodes in single AZ: {az}')
            else:
                # Check for uneven distribution (more than 30% deviation)
                expected_nodes_per_az = total_nodes / num_azs
                max_deviation = 0
                
                for az, node_count in az_distribution.items():
                    deviation = abs(node_count - expected_nodes_per_az) / expected_nodes_per_az
                    max_deviation = max(max_deviation, deviation)
                    
                    if deviation > 0.3:  # 30% deviation threshold
                        issues.append(f'AZ {az} has {node_count} nodes (expected ~{expected_nodes_per_az:.1f}), deviation: {deviation:.1%}')
                        impacted_resources.append(f'AZ {az}: {node_count} nodes (uneven distribution)')
                
                is_compliant = max_deviation <= 0.3
                risk_level = 'medium' if not is_compliant else 'low'
            
            # Build details
            details = {
                'cluster_name': cluster_name,
                'node_distribution': {
                    'total_nodes': total_nodes,
                    'availability_zones': num_azs,
                    'distribution_by_az': az_distribution,
                    'expected_nodes_per_az': round(total_nodes / num_azs, 1) if num_azs > 0 else 0
                },
                'compliance_status': 'compliant' if is_compliant else 'non-compliant',
                'risk_level': risk_level,
                'issues_found': issues
            }
            
            return self._create_check_result('D2', is_compliant, impacted_resources, str(details))
            
        except Exception as e:
            logger.error(f'Error checking multi-AZ node distribution: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            error_details = {
                'error': f'Failed to check multi-AZ node distribution: {str(e)}',
                'cluster_name': cluster_name
            }
            return self._create_check_result('D2', False, [], str(error_details))

    def _check_d3(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D3: Configure Resource Requests/Limits."""
        deployments_without_resources = []
        try:
            logger.info(
                f'Starting resource requests/limits check, namespace: {namespace if namespace else "all"}'
            )

            k8s_client = shared_data.get('k8s_client')

            # Prepare kwargs for filtering
            kwargs = {}
            if namespace:
                kwargs['namespace'] = namespace

            # Use shared deployments (optimization)
            deployments = shared_data.get('deployments', [])

            for deployment in deployments:
                try:
                    deployment_dict = (
                        deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    )

                    metadata = deployment_dict.get('metadata', {})
                    deploy_namespace = metadata.get('namespace', 'default')
                    name = metadata.get('name', 'unknown')

                    # Check containers for resource requests and limits
                    template_spec = (
                        deployment_dict.get('spec', {}).get('template', {}).get('spec', {})
                    )
                    containers = template_spec.get('containers', [])

                    has_complete_resources = True
                    for container in containers:
                        resources = container.get('resources', {})
                        requests = resources.get('requests', {})
                        limits = resources.get('limits', {})
                        
                        # Check if both CPU and memory requests and limits are set
                        if not (requests.get('cpu') and requests.get('memory') and 
                                limits.get('cpu') and limits.get('memory')):
                            has_complete_resources = False
                            break

                    if not has_complete_resources:
                        deployments_without_resources.append(f'{deploy_namespace}/{name}')
                except Exception as deploy_error:
                    logger.error(f'Error processing deployment: {str(deploy_error)}')

            is_compliant = len(deployments_without_resources) == 0
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            detailed_message = f'Found {len(deployments_without_resources)} deployments without complete resource specifications {scope_info}'
            
            return self._create_check_result('D3', is_compliant, deployments_without_resources, detailed_message)

        except Exception as e:
            logger.error(f'Error checking resource requests/limits: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            scope_info = f"in namespace '{namespace}'" if namespace else 'across all namespaces'
            error_message = f'API error while checking resource requests/limits {scope_info}: {str(e)}'
            return self._create_check_result('D3', False, [], error_message)

    def _check_d4(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D4: Namespace ResourceQuotas."""
        namespaces_without_quotas = []
        try:
            logger.info('Starting namespace resource quotas check')

            k8s_client = shared_data.get('k8s_client')

            # Get all namespaces
            namespaces_response = k8s_client.list_resources(kind='Namespace', api_version='v1')
            
            # Get all resource quotas
            quotas_response = k8s_client.list_resources(kind='ResourceQuota', api_version='v1')
            
            # Build set of namespaces that have quotas
            namespaces_with_quotas = set()
            for quota in quotas_response.items:
                try:
                    quota_dict = quota.to_dict() if hasattr(quota, 'to_dict') else quota
                    metadata = quota_dict.get('metadata', {})
                    quota_namespace = metadata.get('namespace')
                    if quota_namespace:
                        namespaces_with_quotas.add(quota_namespace)
                except Exception as quota_error:
                    logger.error(f'Error processing quota: {str(quota_error)}')

            # Check each namespace (excluding system namespaces)
            for ns in namespaces_response.items:
                try:
                    ns_dict = ns.to_dict() if hasattr(ns, 'to_dict') else ns
                    metadata = ns_dict.get('metadata', {})
                    ns_name = metadata.get('name')
                    
                    # Skip system namespaces
                    if ns_name in ['kube-system', 'kube-public', 'kube-node-lease']:
                        continue
                    
                    # If namespace filter is provided, only check that namespace
                    if namespace and ns_name != namespace:
                        continue
                    
                    if ns_name not in namespaces_with_quotas:
                        namespaces_without_quotas.append(ns_name)
                except Exception as ns_error:
                    logger.error(f'Error processing namespace: {str(ns_error)}')

            is_compliant = len(namespaces_without_quotas) == 0
            
            if namespace:
                # If checking specific namespace
                if namespace in namespaces_without_quotas:
                    details = f'Namespace {namespace} does not have ResourceQuota'
                else:
                    details = f'Namespace {namespace} has ResourceQuota configured'
            else:
                # If checking all namespaces
                details = f'Found {len(namespaces_without_quotas)} namespaces without ResourceQuotas'
            
            return self._create_check_result('D4', is_compliant, namespaces_without_quotas, details)

        except Exception as e:
            logger.error(f'Error checking namespace resource quotas: {str(e)}')
            error_message = f'Error checking namespace resource quotas: {str(e)}'
            return self._create_check_result('D4', False, [], error_message)

    def _check_d5(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D5: Namespace LimitRanges."""
        namespaces_without_limits = []
        try:
            logger.info('Starting namespace limit ranges check')

            k8s_client = shared_data.get('k8s_client')

            # Get all namespaces
            namespaces_response = k8s_client.list_resources(kind='Namespace', api_version='v1')
            
            # Get all limit ranges
            limits_response = k8s_client.list_resources(kind='LimitRange', api_version='v1')
            
            # Build set of namespaces that have limit ranges
            namespaces_with_limits = set()
            for limit_range in limits_response.items:
                try:
                    lr_dict = limit_range.to_dict() if hasattr(limit_range, 'to_dict') else limit_range
                    metadata = lr_dict.get('metadata', {})
                    lr_namespace = metadata.get('namespace')
                    if lr_namespace:
                        namespaces_with_limits.add(lr_namespace)
                except Exception as lr_error:
                    logger.error(f'Error processing limit range: {str(lr_error)}')

            # Check each namespace (excluding system namespaces)
            for ns in namespaces_response.items:
                try:
                    ns_dict = ns.to_dict() if hasattr(ns, 'to_dict') else ns
                    metadata = ns_dict.get('metadata', {})
                    ns_name = metadata.get('name')
                    
                    # Skip system namespaces
                    if ns_name in ['kube-system', 'kube-public', 'kube-node-lease']:
                        continue
                    
                    # If namespace filter is provided, only check that namespace
                    if namespace and ns_name != namespace:
                        continue
                    
                    if ns_name not in namespaces_with_limits:
                        namespaces_without_limits.append(ns_name)
                except Exception as ns_error:
                    logger.error(f'Error processing namespace: {str(ns_error)}')

            is_compliant = len(namespaces_without_limits) == 0
            
            if namespace:
                # If checking specific namespace
                if namespace in namespaces_without_limits:
                    details = f'Namespace {namespace} does not have LimitRange'
                else:
                    details = f'Namespace {namespace} has LimitRange configured'
            else:
                # If checking all namespaces
                details = f'Found {len(namespaces_without_limits)} namespaces without LimitRanges'
            
            return self._create_check_result('D5', is_compliant, namespaces_without_limits, details)

        except Exception as e:
            logger.error(f'Error checking namespace limit ranges: {str(e)}')
            error_message = f'Error checking namespace limit ranges: {str(e)}'
            return self._create_check_result('D5', False, [], error_message)

    def _check_d6(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D6: Monitor CoreDNS metrics."""
        try:
            logger.info('Starting CoreDNS metrics check')

            k8s_client = shared_data.get('k8s_client')

            # Check for CoreDNS deployment
            coredns_found = False
            try:
                kube_system_deployments = shared_data.get('kube_system_deployments', [])
                
                for deployment in kube_system_deployments:
                    deployment_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
                    metadata = deployment_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if name == 'coredns':
                        coredns_found = True
                        break
            except Exception as e:
                logger.error(f'Error checking CoreDNS deployment: {str(e)}')

            # Check for monitoring setup (ServiceMonitor or similar)
            monitoring_found = False
            try:
                # Check for Prometheus ServiceMonitor
                k8s_client.api_client.call_api(
                    '/apis/monitoring.coreos.com/v1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                
                servicemonitors_response = k8s_client.list_resources(
                    kind='ServiceMonitor', api_version='monitoring.coreos.com/v1', namespace='kube-system'
                )
                
                for sm in servicemonitors_response.items:
                    sm_dict = sm.to_dict() if hasattr(sm, 'to_dict') else sm
                    metadata = sm_dict.get('metadata', {})
                    name = metadata.get('name', '')
                    if 'coredns' in name.lower():
                        monitoring_found = True
                        break
            except Exception as e:
                logger.info(f'ServiceMonitor not found: {str(e)}')

            is_compliant = coredns_found and monitoring_found
            
            if is_compliant:
                details = 'CoreDNS is deployed and metrics monitoring is configured'
                impacted_resources = []
            elif coredns_found and not monitoring_found:
                details = 'CoreDNS is deployed but metrics monitoring is not configured'
                impacted_resources = ['coredns-metrics']
            else:
                details = 'CoreDNS deployment not found'
                impacted_resources = ['coredns']
            
            return self._create_check_result('D6', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking CoreDNS metrics: {str(e)}')
            error_message = f'Error checking CoreDNS metrics: {str(e)}'
            return self._create_check_result('D6', False, [], error_message)

    def _check_d7(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D7: CoreDNS Configuration."""
        try:
            logger.info(f'Starting CoreDNS configuration check for cluster: {cluster_name}')

            if not cluster_name:
                details = 'No cluster name provided'
                return self._create_check_result('D7', False, [], details)

            k8s_client = shared_data.get('k8s_client')

            # Use shared cluster info and EKS client (optimization)
            eks_client = shared_data.get('eks_client')
            cluster_data = shared_data.get('cluster_info', {})

            # Check if the cluster is an EKS auto mode cluster
            is_auto_mode = False
            compute_config = cluster_data.get('computeConfig', {})
            if compute_config and compute_config.get('enabled', False):
                is_auto_mode = True

            # Check if the cluster is using EKS managed addons for CoreDNS
            has_managed_coredns = False
            try:
                addons = shared_data.get('addons', [])
                for addon in addons:
                    if addon.lower() == 'coredns':
                        has_managed_coredns = True
                        break
            except Exception as e:
                logger.warning(f'Error checking EKS managed addons: {str(e)}')

            # Check for CoreDNS deployment
            coredns_found = False
            try:
                deployments = k8s_client.list_resources(
                    kind='Deployment',
                    api_version='apps/v1',
                    namespace='kube-system',
                    label_selector='k8s-app=kube-dns',
                )

                if hasattr(deployments, 'items') and len(deployments.items) > 0:
                    coredns_found = True
            except Exception as e:
                logger.warning(f'Error checking CoreDNS deployment: {str(e)}')

            # Determine compliance
            if is_auto_mode:
                # Auto mode clusters always have managed CoreDNS
                is_compliant = True
                details = 'EKS auto mode cluster - CoreDNS is automatically managed'
                impacted_resources = []
            elif has_managed_coredns:
                # Regular cluster with managed addon
                is_compliant = True
                details = 'CoreDNS is managed by EKS managed addon'
                impacted_resources = []
            elif coredns_found:
                # Regular cluster with self-managed CoreDNS
                is_compliant = False
                details = 'CoreDNS is self-managed - consider using EKS managed addon'
                impacted_resources = ['coredns']
            else:
                # No CoreDNS found
                is_compliant = False
                details = 'CoreDNS deployment not found'
                impacted_resources = ['coredns']
            
            return self._create_check_result('D7', is_compliant, impacted_resources, details)

        except Exception as e:
            logger.error(f'Error checking CoreDNS configuration: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            error_message = f'Error checking CoreDNS configuration: {str(e)}'
            return self._create_check_result('D7', False, [], error_message)
    def _check_d8(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D8: EKS Node Monitoring Agent add-on.

        Checks if the amazon-eks-node-monitoring-agent add-on is installed.
        Uses shared_data['addons'] already fetched in _initialize_shared_data.
        """
        try:
            logger.info(f'Starting Node Monitoring Agent check for cluster: {cluster_name}')

            # Reuse addons list already fetched for D7
            addons = shared_data.get('addons', [])

            has_node_monitoring = False
            for addon in addons:
                if addon.lower() in ('amazon-eks-node-monitoring-agent', 'eks-node-monitoring-agent'):
                    has_node_monitoring = True
                    break

            if has_node_monitoring:
                return self._create_check_result(
                    'D8',
                    True,
                    [],
                    'EKS Node Monitoring Agent add-on is installed'
                )
            else:
                return self._create_check_result(
                    'D8',
                    False,
                    [cluster_name],
                    'EKS Node Monitoring Agent add-on is not installed. Consider enabling it for enhanced node-level health monitoring and diagnostics.'
                )

        except Exception as e:
            logger.error(f'Error checking Node Monitoring Agent: {str(e)}')
            return self._create_check_result('D8', False, [], f'Error checking Node Monitoring Agent: {str(e)}')

    def _check_d9(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str] = None) -> Dict[str, Any]:
        """Check D9: Kubelet Reserved Resources.

        Compares node.status.capacity vs node.status.allocatable.
        If allocatable equals capacity for CPU or memory, kubelet has no
        reservations configured — system processes compete with pods.

        Samples one node per node group / node pool since all nodes in the
        same group share the same launch template and kubelet config.
        Uses shared_data['nodes'] already fetched for D2.
        """
        try:
            logger.info(f'Starting kubelet reserved resources check for cluster: {cluster_name}')

            nodes = shared_data.get('nodes', [])
            if not nodes:
                return self._create_check_result(
                    'D9', False, [], 'No nodes found in cluster'
                )

            # Group nodes by node group / node pool — check one representative per group
            group_labels = [
                'eks.amazonaws.com/nodegroup',
                'karpenter.sh/nodepool',
                'node.kubernetes.io/instance-type',  # fallback grouping for self-managed
            ]

            seen_groups = set()
            groups_without_reservations = []
            groups_checked = 0

            for node in nodes:
                try:
                    node_dict = node.to_dict() if hasattr(node, 'to_dict') else node
                    metadata = node_dict.get('metadata', {})
                    labels = metadata.get('labels', {})
                    node_name = metadata.get('name', 'unknown')

                    # Determine the group this node belongs to
                    group_key = None
                    group_label_used = None
                    for label in group_labels:
                        if label in labels:
                            group_key = f'{label}={labels[label]}'
                            group_label_used = label
                            break

                    if not group_key:
                        group_key = f'node={node_name}'  # ungrouped node

                    # Skip if we already checked a node from this group
                    if group_key in seen_groups:
                        continue
                    seen_groups.add(group_key)
                    groups_checked += 1

                    status = node_dict.get('status', {})
                    capacity = status.get('capacity', {})
                    allocatable = status.get('allocatable', {})

                    cap_cpu = capacity.get('cpu', '0')
                    alloc_cpu = allocatable.get('cpu', '0')
                    cap_mem = capacity.get('memory', '0')
                    alloc_mem = allocatable.get('memory', '0')

                    # If allocatable equals capacity, no reservations are set
                    if cap_cpu == alloc_cpu and cap_mem == alloc_mem:
                        group_display = labels.get('eks.amazonaws.com/nodegroup') or \
                                        labels.get('karpenter.sh/nodepool') or node_name
                        groups_without_reservations.append(
                            f'{group_display} (sampled node: {node_name}, '
                            f'cpu: {cap_cpu}/{alloc_cpu}, memory: {cap_mem}/{alloc_mem})'
                        )

                except Exception as e:
                    logger.error(f'Error processing node: {str(e)}')

            is_compliant = len(groups_without_reservations) == 0

            if is_compliant:
                details = (
                    f'All {groups_checked} node groups/pools have kubelet resource '
                    f'reservations configured (allocatable < capacity, sampled 1 node per group)'
                )
            else:
                details = (
                    f'Found {len(groups_without_reservations)} of {groups_checked} node groups/pools '
                    f'without kubelet resource reservations (allocatable equals capacity)'
                )

            return self._create_check_result('D9', is_compliant, groups_without_reservations, details)

        except Exception as e:
            logger.error(f'Error checking kubelet reserved resources: {str(e)}')
            return self._create_check_result('D9', False, [], f'Error: {str(e)}')
