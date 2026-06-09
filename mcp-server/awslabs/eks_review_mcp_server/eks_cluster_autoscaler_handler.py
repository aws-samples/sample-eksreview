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

"""Handler for EKS Cluster Autoscaler best practices checks in the EKS MCP Server."""

import json
from pathlib import Path
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_response
from awslabs.eks_review_mcp_server.logging_helper import LogLevel, log_with_request_id
from awslabs.eks_review_mcp_server.models import ClusterAutoscalerCheckResponse
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, Optional, List


class EKSClusterAutoscalerHandler:
    """Handler for EKS Cluster Autoscaler best practices checks in the EKS MCP Server."""

    def __init__(self, mcp, client_cache):
        """Initialize the EKS cluster autoscaler handler.

        Args:
            mcp: The MCP server instance
            client_cache: K8sClientCache instance to share between handlers
        """
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the comprehensive check tool
        self.mcp.tool(name='check_cluster_autoscaler_best_practices')(self.check_cluster_autoscaler_best_practices)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_cluster_autoscaler_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load check registry: {e}")
            return {}

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Get all checks flattened into a single dictionary."""
        return self.check_registry.get('cluster_autoscaler_checks', {})

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
            'severity': check_info.get('severity', 'Medium'),
            'compliant': compliant,
            'impacted_resources': impacted_resources,
            'details': details,
        }

    def _create_check_error_result(self, check_id: str, error_msg: str) -> Dict[str, Any]:
        """Create an error result for a failed check."""
        check_info = self._get_check_info(check_id)
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'Medium'),
            'compliant': False,
            'impacted_resources': [],
            'details': f'Check failed with error: {error_msg}',
        }

    def _create_error_response(self, cluster_name: str, error_msg: str) -> ClusterAutoscalerCheckResponse:
        """Create an error response."""
        return ClusterAutoscalerCheckResponse(
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

    async def check_cluster_autoscaler_best_practices(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for Cluster Autoscaler best practices.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
        namespace: Optional[str] = Field(
            'kube-system', description='Namespace where Cluster Autoscaler is deployed (default: kube-system).'
        ),
    ) -> ClusterAutoscalerCheckResponse:
        """Check EKS cluster for Cluster Autoscaler best practices.

        This tool runs a comprehensive set of checks against your EKS cluster's
        Cluster Autoscaler configuration based on AWS best practices to identify 
        potential issues and provides remediation guidance.

        The tool evaluates critical best practices across:
        - Version Compatibility: Ensures CA version matches cluster version
        - Auto Discovery: Verifies proper auto-discovery configuration and tags
        - IAM Permissions: Validates least-privileged IAM role setup
        - Node Group Configuration: Checks for optimal node group setup
        - Cost Optimization: Reviews Spot instance and expander configurations
        - Performance & Scalability: Assesses resource allocation and scan intervals
        - Availability: Evaluates overprovisioning and workload protection
        """
        try:
            logger.info(f'Starting Cluster Autoscaler best practices check for cluster: {cluster_name}')

            # Pre-initialize clients and fetch shared data for efficiency
            clients = await self._initialize_clients(cluster_name, region, namespace)
            if not clients:
                return self._create_error_response(cluster_name, "Failed to initialize required clients")

            # Get cluster and node group info once for sharing between checks
            shared_data = await self._get_cluster_and_nodegroup_info(
                clients['eks'], 
                clients['ec2'], 
                clients['autoscaling'], 
                clients['k8s'], 
                cluster_name, 
                namespace
            )
            if shared_data:
                clients['shared_data'] = shared_data
                logger.info(
                    f'Retrieved cluster info: version={shared_data.get("cluster_version")}, '
                    f'managed={shared_data.get("managed_count", 0)}, '
                    f'self-managed={shared_data.get("self_managed_count", 0)}, '
                    f'total={shared_data.get("total_node_groups", 0)}'
                )

            # First check if Cluster Autoscaler is deployed (C1 check)
            ca_deployment_result = await self._check_version_compatibility(clients, cluster_name, namespace)
            
            check_results = []
            all_compliant = True
            
            # Check if Cluster Autoscaler was found
            ca_found = ca_deployment_result['compliant'] or (
                ca_deployment_result['impacted_resources'] and 
                len(ca_deployment_result['impacted_resources']) > 0
            )
            
            # Early exit if Auto Mode is detected (optimization #4)
            if shared_data and shared_data.get('skip_ca_checks'):
                logger.info('Auto Mode detected - skipping Cluster Autoscaler checks')
                auto_mode_features = shared_data.get('auto_mode_features', {})
                enabled_features = [k for k, v in auto_mode_features.items() if v]
                
                check_results.append(self._create_check_result(
                    'C1',
                    True,
                    [],
                    f'EKS Auto Mode is enabled ({", ".join(enabled_features)}) - Cluster Autoscaler checks not applicable'
                ))
                all_compliant = True
                
                passed_count = 1
                failed_count = 0
                summary = f'Cluster {cluster_name} uses EKS Auto Mode - Cluster Autoscaler checks not applicable'
                
                return ClusterAutoscalerCheckResponse(
                    isError=False,
                    content=[TextContent(type='text', text=summary)],
                    check_results=check_results,
                    overall_compliant=all_compliant,
                    summary=summary,
                )
            
            # If Cluster Autoscaler is deployed, run all checks
            if ca_found:
                logger.info('Cluster Autoscaler found - running Cluster Autoscaler best practices checks')
                
                # Add the version compatibility check result
                check_results.append(ca_deployment_result)
                
                # Get remaining checks (C2-C14) and sort by ID
                all_checks = self._get_all_checks()
                remaining_checks = {k: v for k, v in all_checks.items() if k != 'C1'}
                
                for check_id in sorted(remaining_checks.keys()):
                    try:
                        logger.info(f'Running check {check_id}')
                        result = await self._execute_check(check_id, clients, cluster_name, namespace)
                        check_results.append(result)
                        
                        if not result['compliant']:
                            all_compliant = False
                            
                        logger.info(f'Check {check_id} completed: {result["compliant"]}')
                        
                    except Exception as e:
                        logger.error(f'Error in check {check_id}: {str(e)}')
                        error_result = self._create_check_error_result(check_id, str(e))
                        check_results.append(error_result)
                        all_compliant = False
            else:
                logger.info('Cluster Autoscaler not found - checking for alternative autoscaling solutions')
                
                # Check if Karpenter or Auto Mode is being used (reuse shared_data)
                karpenter_found = await self._check_for_karpenter(clients['k8s'], namespace)
                auto_mode_enabled = await self._check_for_auto_mode(clients['eks'], cluster_name, shared_data)
                
                if karpenter_found or auto_mode_enabled:
                    alternative = 'Karpenter' if karpenter_found else 'EKS Auto Mode'
                    logger.info(f'{alternative} detected - Cluster Autoscaler checks not applicable')
                    check_results.append(self._create_check_result(
                        'C1',
                        True,
                        [],
                        f'Cluster Autoscaler not found, but {alternative} is being used for node autoscaling'
                    ))
                    all_compliant = True  # Using alternative is compliant
                else:
                    logger.info('No autoscaling solution found - adding Cluster Autoscaler deployment check')
                    check_results.append(ca_deployment_result)
                    all_compliant = False

            # Generate summary
            passed_count = sum(1 for r in check_results if r['compliant'])
            failed_count = len(check_results) - passed_count
            summary = f'Cluster {cluster_name} Cluster Autoscaler check: {passed_count} checks passed, {failed_count} checks failed'

            # Create detailed response with JSON data in content
            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return ClusterAutoscalerCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in Cluster Autoscaler check: {str(e)}')
            return self._create_error_response(cluster_name, str(e))

    async def _initialize_clients(self, cluster_name: str, region: Optional[str], namespace: Optional[str]) -> Optional[Dict[str, Any]]:
        """Initialize all required clients for Cluster Autoscaler checks."""
        try:
            clients = {}
            
            # Initialize AWS EKS client
            try:
                clients['eks'] = AwsHelper.create_boto3_client('eks', region_name=region)
                logger.info('Successfully initialized EKS client')
            except Exception as e:
                logger.error(f'Failed to initialize EKS client: {str(e)}')
                return None
            
            # Initialize AWS EC2 client (for self-managed node groups)
            try:
                clients['ec2'] = AwsHelper.create_boto3_client('ec2', region_name=region)
                logger.info('Successfully initialized EC2 client')
            except Exception as e:
                logger.error(f'Failed to initialize EC2 client: {str(e)}')
                return None
            
            # Initialize AWS Auto Scaling client (for self-managed node groups)
            try:
                clients['autoscaling'] = AwsHelper.create_boto3_client('autoscaling', region_name=region)
                logger.info('Successfully initialized Auto Scaling client')
            except Exception as e:
                logger.error(f'Failed to initialize Auto Scaling client: {str(e)}')
                return None
            
            # Initialize Kubernetes client
            try:
                clients['k8s'] = self.client_cache.get_client(cluster_name, region=region)
                logger.info('Successfully initialized Kubernetes client')
            except Exception as e:
                logger.error(f'Failed to initialize Kubernetes client: {str(e)}')
                return None
            
            return clients
            
        except Exception as e:
            logger.error(f'Error initializing clients: {str(e)}')
            return None

    async def _get_cluster_and_nodegroup_info(self, eks_client, ec2_client, autoscaling_client, k8s_client, cluster_name: str, namespace: Optional[str]) -> Optional[Dict[str, Any]]:
        """Fetch cluster and all node group details (managed + self-managed) once for sharing between checks."""
        try:
            shared_data = {}
            
            # Get cluster info (single API call - reused throughout)
            try:
                cluster_response = eks_client.describe_cluster(name=cluster_name)
                cluster_info = cluster_response['cluster']
                shared_data['cluster_version'] = cluster_info['version']
                shared_data['cluster_info'] = cluster_info  # Store full cluster object for reuse
                logger.info(f'Cluster version: {shared_data["cluster_version"]}')
                
                # Check for Auto Mode early (optimization #4)
                compute_config = cluster_info.get('computeConfig', {})
                storage_config = cluster_info.get('storageConfig', {})
                kubernetes_network_config = cluster_info.get('kubernetesNetworkConfig', {})
                elastic_load_balancing = kubernetes_network_config.get('elasticLoadBalancing', {})
                
                is_auto_mode = (
                    compute_config.get('enabled', False) or
                    storage_config.get('blockStorage', {}).get('enabled', False) or
                    elastic_load_balancing.get('enabled', False)
                )
                
                shared_data['is_auto_mode'] = is_auto_mode
                
                if is_auto_mode:
                    logger.info('EKS Auto Mode detected - Cluster Autoscaler checks not applicable')
                    shared_data['skip_ca_checks'] = True
                    shared_data['auto_mode_features'] = {
                        'compute_enabled': compute_config.get('enabled', False),
                        'storage_enabled': storage_config.get('blockStorage', {}).get('enabled', False),
                        'elastic_load_balancing_enabled': elastic_load_balancing.get('enabled', False)
                    }
                    # Early exit - no need to fetch node groups for Auto Mode
                    return shared_data
                    
            except Exception as e:
                logger.warning(f'Failed to get cluster info: {str(e)}')
                shared_data['cluster_version'] = None
                shared_data['cluster_info'] = None
                shared_data['is_auto_mode'] = False
            
            # Get all EKS managed node groups (optimization #5: extract only needed fields)
            managed_node_groups = []
            managed_asg_names = set()
            try:
                ng_list = eks_client.list_nodegroups(clusterName=cluster_name)
                node_group_names = ng_list.get('nodegroups', [])
                logger.info(f'Found {len(node_group_names)} EKS managed node groups')
                
                # Fetch details for all managed node groups
                for ng_name in node_group_names:
                    try:
                        ng_details = eks_client.describe_nodegroup(
                            clusterName=cluster_name,
                            nodegroupName=ng_name
                        )
                        nodegroup = ng_details['nodegroup']
                        
                        # Extract only needed fields (optimization #5)
                        managed_node_groups.append({
                            'type': 'managed',
                            'name': ng_name,
                            'tags': nodegroup.get('tags', {}),
                            'capacity_type': nodegroup.get('capacityType'),
                            'instance_types': nodegroup.get('instanceTypes', []),
                            'scaling_config': nodegroup.get('scalingConfig', {}),
                            'labels': nodegroup.get('labels', {}),
                            'taints': nodegroup.get('taints', []),
                            'ami_type': nodegroup.get('amiType'),
                            'node_role': nodegroup.get('nodeRole'),
                            'resources': nodegroup.get('resources', {})
                        })
                        
                        # Track managed ASG names to exclude them from self-managed list
                        resources = nodegroup.get('resources', {})
                        for asg in resources.get('autoScalingGroups', []):
                            managed_asg_names.add(asg.get('name'))
                            
                    except Exception as ng_error:
                        logger.warning(f'Failed to get details for managed node group {ng_name}: {str(ng_error)}')
            
            except Exception as e:
                logger.warning(f'Failed to get managed node groups: {str(e)}')
            
            # Get self-managed node groups using Kubernetes + EC2 approach (networking handler pattern)
            self_managed_node_groups = []
            all_asg_names = set(managed_asg_names)  # Start with managed ASG names
            try:
                # Step 1: Get all nodes from Kubernetes (works for all node types)
                nodes = k8s_client.list_resources(kind='Node', api_version='v1')
                logger.info(f'Found {len(nodes.items)} total nodes in cluster')
                shared_data['node_count'] = len(nodes.items)
                
                # Step 2: Extract instance IDs from node labels
                instance_ids = []
                for node in nodes.items:
                    node_dict = node.to_dict() if hasattr(node, 'to_dict') else node
                    labels = node_dict.get('metadata', {}).get('labels', {})
                    instance_id = labels.get('node.kubernetes.io/instance-id')
                    if instance_id:
                        instance_ids.append(instance_id)
                
                logger.info(f'Extracted {len(instance_ids)} instance IDs from nodes')
                
                # Step 3: Query EC2 for instance details with pagination (optimization #6)
                if instance_ids:
                    # Use paginator for large clusters (optimization #6)
                    paginator = ec2_client.get_paginator('describe_instances')
                    page_iterator = paginator.paginate(InstanceIds=instance_ids)
                    
                    # Extract ASG names from instances
                    for page in page_iterator:
                        for reservation in page['Reservations']:
                            for instance in reservation['Instances']:
                                for tag in instance.get('Tags', []):
                                    if tag['Key'] == 'aws:autoscaling:groupName':
                                        all_asg_names.add(tag['Value'])
                                        break
                    
                    logger.info(f'Found {len(all_asg_names)} total ASGs (managed + self-managed)')
                
                # Step 4: Query ALL ASGs in a single call (managed + self-managed)
                if all_asg_names:
                    asgs_response = autoscaling_client.describe_auto_scaling_groups(
                        AutoScalingGroupNames=list(all_asg_names)
                    )
                    
                    # Build ASG name -> tags mapping for all ASGs
                    all_asg_tags_map = {}
                    for asg in asgs_response['AutoScalingGroups']:
                        asg_name = asg['AutoScalingGroupName']
                        tags = {tag['Key']: tag['Value'] for tag in asg.get('Tags', [])}
                        all_asg_tags_map[asg_name] = tags
                        
                        # If self-managed (not in managed_asg_names), add to self_managed list
                        if asg_name not in managed_asg_names:
                            # Extract instance types and capacity type from ASG config
                            instance_types = []
                            capacity_type = 'ON_DEMAND'  # default
                            
                            # Check MixedInstancesPolicy first (most common for diversified ASGs)
                            mip = asg.get('MixedInstancesPolicy', {})
                            if mip:
                                lt_overrides = mip.get('LaunchTemplate', {}).get('Overrides', [])
                                for override in lt_overrides:
                                    it = override.get('InstanceType')
                                    if it:
                                        instance_types.append(it)
                                # Check if Spot is configured
                                instances_dist = mip.get('InstancesDistribution', {})
                                on_demand_pct = instances_dist.get('OnDemandPercentageAboveBaseCapacity', 100)
                                if on_demand_pct < 100:
                                    capacity_type = 'SPOT'
                            
                            # Fallback: check LaunchTemplate or LaunchConfiguration
                            if not instance_types:
                                # ASG instances list can tell us the instance type
                                for inst in asg.get('Instances', [])[:1]:
                                    it = inst.get('InstanceType')
                                    if it:
                                        instance_types.append(it)
                            
                            self_managed_node_groups.append({
                                'type': 'self_managed',
                                'name': asg_name,
                                'details': asg,
                                'tags': tags,
                                'instance_types': instance_types,
                                'capacity_type': capacity_type,
                                'has_ca_enabled': tags.get('k8s.io/cluster-autoscaler/enabled') == 'true',
                                'has_ca_cluster_tag': f'k8s.io/cluster-autoscaler/{cluster_name}' in tags
                            })
                    
                    # Update managed node groups with ASG tags (for C3 check)
                    for ng in managed_node_groups:
                        ng_resources = ng.get('resources', {})
                        for asg_ref in ng_resources.get('autoScalingGroups', []):
                            asg_name = asg_ref.get('name', '')
                            if asg_name in all_asg_tags_map:
                                ng['asg_tags'] = all_asg_tags_map[asg_name]
                                break
                    
                    logger.info(f'Queried {len(all_asg_tags_map)} ASGs in single call: '
                                f'{len(managed_asg_names)} managed, {len(self_managed_node_groups)} self-managed')
                
            except Exception as e:
                logger.warning(f'Failed to get self-managed node groups: {str(e)}')
                import traceback
                logger.warning(f'Traceback: {traceback.format_exc()}')
            
            # Store both types of node groups
            shared_data['managed_node_groups'] = managed_node_groups
            shared_data['self_managed_node_groups'] = self_managed_node_groups
            shared_data['node_groups'] = managed_node_groups  # For backward compatibility with C3 check
            shared_data['all_node_groups'] = managed_node_groups + self_managed_node_groups
            shared_data['managed_count'] = len(managed_node_groups)
            shared_data['self_managed_count'] = len(self_managed_node_groups)
            shared_data['total_node_groups'] = len(managed_node_groups) + len(self_managed_node_groups)
            
            logger.info(f'Node group summary: {len(managed_node_groups)} managed, {len(self_managed_node_groups)} self-managed, {shared_data["total_node_groups"]} total')
            
            # Get Cluster Autoscaler deployment info and parse configuration (optimization #2)
            try:
                deployments = k8s_client.list_resources(
                    kind='Deployment',
                    api_version='apps/v1',
                    namespace=namespace or 'kube-system'
                )
                
                ca_deployments = []
                ca_config = {
                    'auto_discovery_enabled': False,
                    'expander_strategy': None,
                    'scan_interval': None,
                    'scale_down_enabled': True,
                    'scale_down_delay_after_add': None,
                    'scale_down_unneeded_time': None,
                    'resource_limits': {},
                    'resource_requests': {},
                    'command_args': [],
                    'env_vars': {}
                }
                
                for deployment in deployments.items:
                    if 'cluster-autoscaler' in deployment.metadata.name.lower():
                        ca_deployments.append({
                            'name': deployment.metadata.name,
                            'namespace': deployment.metadata.namespace,
                            'deployment': deployment
                        })
                        
                        # Pre-extract CA configuration (optimization #2)
                        containers = deployment.spec.template.spec.get('containers', [])
                        for container in containers:
                            if 'cluster-autoscaler' in container.get('name', '').lower():
                                # Parse command args
                                command = container.get('command', [])
                                args = container.get('args', [])
                                all_args = command + args
                                ca_config['command_args'] = all_args
                                
                                # Extract key settings
                                for arg in all_args:
                                    arg_str = str(arg)
                                    if '--node-group-auto-discovery' in arg_str:
                                        ca_config['auto_discovery_enabled'] = True
                                    elif '--expander=' in arg_str:
                                        ca_config['expander_strategy'] = arg_str.split('=', 1)[1] if '=' in arg_str else None
                                    elif '--scan-interval=' in arg_str:
                                        ca_config['scan_interval'] = arg_str.split('=', 1)[1] if '=' in arg_str else None
                                    elif '--scale-down-enabled=' in arg_str:
                                        ca_config['scale_down_enabled'] = arg_str.split('=', 1)[1].lower() == 'true' if '=' in arg_str else True
                                    elif '--scale-down-delay-after-add=' in arg_str:
                                        ca_config['scale_down_delay_after_add'] = arg_str.split('=', 1)[1] if '=' in arg_str else None
                                    elif '--scale-down-unneeded-time=' in arg_str:
                                        ca_config['scale_down_unneeded_time'] = arg_str.split('=', 1)[1] if '=' in arg_str else None
                                
                                # Extract resource limits and requests
                                resources = container.get('resources', {})
                                ca_config['resource_limits'] = resources.get('limits', {})
                                ca_config['resource_requests'] = resources.get('requests', {})
                                
                                # Extract environment variables
                                env_vars = container.get('env', [])
                                for env_var in env_vars:
                                    ca_config['env_vars'][env_var.get('name')] = env_var.get('value')
                
                shared_data['ca_deployments'] = ca_deployments
                shared_data['ca_config'] = ca_config  # Pre-parsed configuration
                logger.info(f'Found {len(ca_deployments)} Cluster Autoscaler deployments')
                logger.info(f'CA config: auto_discovery={ca_config["auto_discovery_enabled"]}, expander={ca_config["expander_strategy"]}, scan_interval={ca_config["scan_interval"]}')
                
            except Exception as e:
                logger.warning(f'Failed to get Cluster Autoscaler deployments: {str(e)}')
                shared_data['ca_deployments'] = []
                shared_data['ca_config'] = {}
            
            return shared_data
            
        except Exception as e:
            logger.warning(f'Failed to get cluster and node group info: {str(e)}')
            import traceback
            logger.warning(f'Traceback: {traceback.format_exc()}')
            return None

    async def _execute_check(self, check_id: str, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Execute a single check based on its ID."""
        
        # Map check IDs to their corresponding methods
        check_methods = {
            'C1': self._check_version_compatibility,
            'C2': self._check_auto_discovery_enabled,
            'C3': self._check_node_group_tags,
            'C4': self._check_iam_permissions,
            'C5': self._check_identical_scheduling_properties,
            'C6': self._check_node_group_consolidation,
            'C7': self._check_managed_node_groups,
            'C8': self._check_spot_diversification,
            'C9': self._check_capacity_separation,
            'C10': self._check_expander_strategy,
            'C11': self._check_resource_allocation,
            'C12': self._check_scan_interval,
            'C13': self._check_overprovisioning,
            'C14': self._check_workload_protection,
        }
        
        method = check_methods.get(check_id)
        if method:
            return await method(clients, cluster_name, namespace)
        else:
            return self._create_check_error_result(check_id, f'Check method not implemented for {check_id}')

    async def _check_version_compatibility(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if Cluster Autoscaler version matches cluster version."""
        try:
            # Use shared data if available
            shared_data = clients.get('shared_data', {})
            cluster_version = shared_data.get('cluster_version')
            ca_deployments = shared_data.get('ca_deployments', [])
            
            if not cluster_version:
                return self._create_check_error_result('C1', 'Failed to get cluster version')
            
            if not ca_deployments:
                # Fallback: try to fetch deployments directly
                k8s_client = clients.get('k8s')
                if not k8s_client:
                    return self._create_check_error_result('C1', 'Kubernetes client not available')
                
                deployments = k8s_client.list_resources(
                    kind='Deployment',
                    api_version='apps/v1',
                    namespace=namespace or 'kube-system'
                )
                
                ca_deployments = []
                for deployment in deployments.items:
                    if 'cluster-autoscaler' in deployment.metadata.name.lower():
                        ca_deployments.append({
                            'name': deployment.metadata.name,
                            'namespace': deployment.metadata.namespace,
                            'deployment': deployment
                        })
            
            version_issues = []
            compliant_deployments = []
            
            for ca_dep in ca_deployments:
                deployment = ca_dep['deployment']
                deployment_name = f"{ca_dep['namespace']}/{ca_dep['name']}"
                containers = deployment.spec.template.spec.get('containers', [])
                
                for container in containers:
                    if 'cluster-autoscaler' in container.get('name', '').lower():
                        image = container.get('image', '')
                        
                        # Extract version from image tag
                        if ':v' in image:
                            ca_version = image.split(':v')[1].split('.')[0] + '.' + image.split(':v')[1].split('.')[1]
                            if ca_version == cluster_version:
                                compliant_deployments.append(deployment_name)
                            else:
                                version_issues.append(f"{deployment_name} - CA version {ca_version} != cluster version {cluster_version}")
                        else:
                            version_issues.append(f"{deployment_name} - cannot determine CA version from image {image}")
            
            if not compliant_deployments and not version_issues:
                return self._create_check_result(
                    'C1',
                    False,
                    [],
                    'No Cluster Autoscaler deployment found'
                )
            
            if version_issues:
                return self._create_check_result(
                    'C1',
                    False,
                    version_issues,
                    f'Version mismatch detected. Cluster version: {cluster_version}'
                )
            else:
                return self._create_check_result(
                    'C1',
                    True,
                    compliant_deployments,
                    f'Cluster Autoscaler version matches cluster version {cluster_version}'
                )
        except Exception as e:
            return self._create_check_error_result('C1', str(e))

    async def _check_auto_discovery_enabled(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if auto-discovery is enabled."""
        try:
            # Use shared data if available
            shared_data = clients.get('shared_data', {})
            ca_deployments = shared_data.get('ca_deployments', [])
            
            if not ca_deployments:
                # Fallback: try to fetch deployments directly
                k8s_client = clients.get('k8s')
                if not k8s_client:
                    return self._create_check_error_result('C2', 'Kubernetes client not available')
                
                deployments = k8s_client.list_resources(
                    kind='Deployment',
                    api_version='apps/v1',
                    namespace=namespace or 'kube-system'
                )
                
                ca_deployments = []
                for deployment in deployments.items:
                    if 'cluster-autoscaler' in deployment.metadata.name.lower():
                        ca_deployments.append({
                            'name': deployment.metadata.name,
                            'namespace': deployment.metadata.namespace,
                            'deployment': deployment
                        })
            
            auto_discovery_issues = []
            compliant_deployments = []
            
            for ca_dep in ca_deployments:
                deployment = ca_dep['deployment']
                deployment_name = f"{ca_dep['namespace']}/{ca_dep['name']}"
                containers = deployment.spec.template.spec.get('containers', [])
                
                for container in containers:
                    if 'cluster-autoscaler' in container.get('name', '').lower():
                        command = container.get('command', [])
                        args = container.get('args', [])
                        all_args = command + args
                        
                        # Check for auto-discovery configuration
                        has_auto_discovery = any('--node-group-auto-discovery' in str(arg) for arg in all_args)
                        
                        if not has_auto_discovery:
                            auto_discovery_issues.append(f"{deployment_name} - auto-discovery not enabled")
                        else:
                            compliant_deployments.append(deployment_name)
            
            if not compliant_deployments and not auto_discovery_issues:
                return self._create_check_result(
                    'C2',
                    False,
                    [],
                    'No Cluster Autoscaler deployment found'
                )
            
            if auto_discovery_issues:
                return self._create_check_result(
                    'C2',
                    False,
                    auto_discovery_issues,
                    'Auto-discovery not enabled'
                )
            else:
                return self._create_check_result(
                    'C2',
                    True,
                    compliant_deployments,
                    'Auto-discovery is enabled'
                )
        except Exception as e:
            return self._create_check_error_result('C2', str(e))

    async def _check_node_group_tags(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if node groups have proper auto-discovery tags (both managed and self-managed)."""
        try:
            # Use shared data if available
            shared_data = clients.get('shared_data', {})
            managed_node_groups = shared_data.get('managed_node_groups', [])
            self_managed_node_groups = shared_data.get('self_managed_node_groups', [])
            
            total_node_groups = len(managed_node_groups) + len(self_managed_node_groups)
            
            if total_node_groups == 0:
                return self._create_check_result(
                    'C3',
                    False,
                    [],
                    'No node groups found in the cluster'
                )
            
            missing_tags = []
            compliant_nodegroups = []
            
            # Check managed node groups — use ASG tags (CA discovers via ASG tags, not EKS nodegroup tags)
            for ng in managed_node_groups:
                ng_name = ng.get('name', 'unknown')
                tags = ng.get('asg_tags', ng.get('tags', {}))
                
                # Check for required auto-discovery tags on the ASG
                has_cluster_tag = f'k8s.io/cluster-autoscaler/{cluster_name}' in tags
                has_enabled_tag = tags.get('k8s.io/cluster-autoscaler/enabled') == 'true'
                
                if not has_cluster_tag or not has_enabled_tag:
                    missing_tags.append(f'Managed: {ng_name}')
                else:
                    compliant_nodegroups.append(f'Managed: {ng_name}')
            
            # Check self-managed node groups (ASGs)
            for ng in self_managed_node_groups:
                ng_name = ng.get('name', 'unknown')
                tags = ng.get('tags', {})
                
                # Check for required auto-discovery tags
                has_cluster_tag = f'k8s.io/cluster-autoscaler/{cluster_name}' in tags
                has_enabled_tag = tags.get('k8s.io/cluster-autoscaler/enabled') == 'true'
                
                if not has_cluster_tag or not has_enabled_tag:
                    missing_tags.append(f'Self-managed: {ng_name}')
                else:
                    compliant_nodegroups.append(f'Self-managed: {ng_name}')
            
            if missing_tags:
                return self._create_check_result(
                    'C3',
                    False,
                    missing_tags,
                    f'Found {len(missing_tags)} node groups without proper auto-discovery tags (out of {total_node_groups} total)'
                )
            else:
                return self._create_check_result(
                    'C3',
                    True,
                    compliant_nodegroups,
                    f'All {len(compliant_nodegroups)} node groups have proper auto-discovery tags ({len(managed_node_groups)} managed, {len(self_managed_node_groups)} self-managed)'
                )
        except Exception as e:
            return self._create_check_error_result('C3', str(e))

    # Placeholder implementations for remaining checks
    async def _check_iam_permissions(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C4: Employ least privileged IAM access for Cluster Autoscaler.
        
        Per AWS best practices, when auto-discovery is used, the IAM policy for
        SetDesiredCapacity and TerminateInstanceInAutoScalingGroup should be scoped
        using resource tag conditions, not Resource: * without conditions.
        """
        try:
            shared_data = clients.get('shared_data', {})
            ca_deployments = shared_data.get('ca_deployments', [])
            k8s_client = clients.get('k8s')
            
            if not ca_deployments:
                return self._create_check_result('C4', True, [],
                    'Cluster Autoscaler not deployed — check not applicable')
            
            # Step 1: Get the CA service account name from the deployment
            ca_dep = ca_deployments[0]
            deployment = ca_dep.get('deployment')
            dep_dict = deployment.to_dict() if hasattr(deployment, 'to_dict') else deployment
            sa_name = dep_dict.get('spec', {}).get('template', {}).get('spec', {}).get('serviceAccountName', 'default')
            ca_namespace = ca_dep.get('namespace', namespace or 'kube-system')
            
            logger.info(f'CA service account: {ca_namespace}/{sa_name}')
            
            # Step 2: Get the service account's IAM role ARN
            role_arn = None
            try:
                sa_response = k8s_client.list_resources(
                    kind='ServiceAccount', api_version='v1',
                    namespace=ca_namespace, field_selector=f'metadata.name={sa_name}'
                )
                if hasattr(sa_response, 'items') and sa_response.items:
                    sa_dict = sa_response.items[0].to_dict() if hasattr(sa_response.items[0], 'to_dict') else sa_response.items[0]
                    annotations = sa_dict.get('metadata', {}).get('annotations', {})
                    role_arn = annotations.get('eks.amazonaws.com/role-arn')
            except Exception as e:
                logger.warning(f'Failed to get CA service account: {str(e)}')
            
            if not role_arn:
                return self._create_check_result('C4', False,
                    [f'{ca_namespace}/{sa_name}'],
                    'Cluster Autoscaler service account has no IAM role (IRSA) configured. '
                    'CA may be using the node instance role which is overly broad.')
            
            logger.info(f'CA IAM role ARN: {role_arn}')
            
            # Step 3: Extract role name from ARN and check IAM policies
            role_name = role_arn.split('/')[-1]
            iam_client = AwsHelper.create_boto3_client('iam')
            
            issues = []
            impacted = []
            
            # Dangerous actions that must be scoped with conditions
            DANGEROUS_ACTIONS = {
                'autoscaling:SetDesiredCapacity',
                'autoscaling:TerminateInstanceInAutoScalingGroup',
                'autoscaling:*',
            }
            
            def _check_policy_document(policy_doc, policy_name):
                """Check a policy document for overly broad autoscaling permissions."""
                statements = policy_doc.get('Statement', [])
                for stmt in statements:
                    if stmt.get('Effect') != 'Allow':
                        continue
                    
                    actions = stmt.get('Action', [])
                    if isinstance(actions, str):
                        actions = [actions]
                    
                    # Check if any dangerous actions are present
                    has_dangerous = any(
                        a in DANGEROUS_ACTIONS or a == '*'
                        for a in actions
                    )
                    if not has_dangerous:
                        continue
                    
                    # Dangerous actions found — check if scoped
                    resource = stmt.get('Resource', '*')
                    condition = stmt.get('Condition', {})
                    
                    # If Resource is * and no Condition, it's overly broad
                    if resource == '*' or resource == ['*']:
                        if not condition:
                            matched_actions = [a for a in actions if a in DANGEROUS_ACTIONS or a == '*']
                            issues.append(
                                f'Policy "{policy_name}": actions {matched_actions} '
                                f'have Resource: * without tag-based Condition scoping'
                            )
                            impacted.append(f'IAM Policy: {policy_name}')
                            return
                        
                        # Has condition — check if it uses resource tags
                        has_tag_condition = False
                        for cond_type, cond_values in condition.items():
                            for cond_key in cond_values:
                                if 'aws:ResourceTag/' in cond_key:
                                    has_tag_condition = True
                                    break
                        
                        if not has_tag_condition:
                            matched_actions = [a for a in actions if a in DANGEROUS_ACTIONS or a == '*']
                            issues.append(
                                f'Policy "{policy_name}": actions {matched_actions} '
                                f'have Condition but not scoped by aws:ResourceTag'
                            )
                            impacted.append(f'IAM Policy: {policy_name}')
            
            # Check inline policies
            try:
                inline_response = iam_client.list_role_policies(RoleName=role_name)
                for policy_name in inline_response.get('PolicyNames', []):
                    policy_response = iam_client.get_role_policy(
                        RoleName=role_name, PolicyName=policy_name
                    )
                    _check_policy_document(policy_response.get('PolicyDocument', {}), policy_name)
            except Exception as e:
                logger.warning(f'Failed to check inline policies: {str(e)}')
            
            # Check attached managed policies
            try:
                attached_response = iam_client.list_attached_role_policies(RoleName=role_name)
                for policy in attached_response.get('AttachedPolicies', []):
                    policy_arn = policy['PolicyArn']
                    policy_name = policy['PolicyName']
                    
                    try:
                        # Get the default policy version
                        policy_meta = iam_client.get_policy(PolicyArn=policy_arn)
                        version_id = policy_meta['Policy']['DefaultVersionId']
                        
                        version_response = iam_client.get_policy_version(
                            PolicyArn=policy_arn, VersionId=version_id
                        )
                        doc = version_response['PolicyVersion']['Document']
                        _check_policy_document(doc, policy_name)
                    except Exception as e:
                        logger.warning(f'Failed to check managed policy {policy_name}: {str(e)}')
            except Exception as e:
                logger.warning(f'Failed to list attached policies: {str(e)}')
            
            if issues:
                return self._create_check_result('C4', False, impacted,
                    f'CA IAM role "{role_name}" has overly broad permissions: {"; ".join(issues)}. '
                    f'Scope SetDesiredCapacity and TerminateInstanceInAutoScalingGroup '
                    f'using aws:ResourceTag conditions per AWS best practices.')
            else:
                return self._create_check_result('C4', True, [f'IAM Role: {role_name}'],
                    f'CA IAM role "{role_name}" uses least-privileged access with tag-based scoping')
        
        except Exception as e:
            return self._create_check_error_result('C4', str(e))

    async def _check_identical_scheduling_properties(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C5: Identical scheduling properties within node groups.
        
        Per AWS best practices, each node in a node group should have identical
        scheduling properties. For MixedInstancePolicies, instance types must be
        of the same shape for CPU, Memory, and GPU.
        """
        try:
            shared_data = clients.get('shared_data', {})
            all_ngs = shared_data.get('all_node_groups', [])
            
            if not all_ngs:
                return self._create_check_result('C5', True, [],
                    'No node groups found — check not applicable')
            
            issues = []
            for ng in all_ngs:
                ng_name = ng.get('name', 'unknown')
                ng_type = ng.get('type', 'unknown')
                instance_types = ng.get('instance_types', [])
                
                if len(instance_types) > 1:
                    prefix = 'Managed' if ng_type == 'managed' else 'Self-managed'
                    issues.append(f'{prefix}: {ng_name} (types: {", ".join(instance_types)})')
            
            if issues:
                return self._create_check_result('C5', False, issues,
                    f'Found {len(issues)} node groups with multiple instance types — '
                    f'verify they have identical CPU/Memory/GPU shapes')
            return self._create_check_result('C5', True, [],
                'All node groups use consistent instance types with identical scheduling properties')
        except Exception as e:
            return self._create_check_error_result('C5', str(e))

    async def _check_node_group_consolidation(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C6: Prefer fewer node groups with many nodes.
        
        Many node groups with few nodes degrades CA scalability.
        """
        try:
            shared_data = clients.get('shared_data', {})
            total_ngs = shared_data.get('total_node_groups', 0)
            
            # Threshold: more than 20 node groups is a scalability concern
            if total_ngs > 20:
                return self._create_check_result('C6', False,
                    [f'Total node groups: {total_ngs}'],
                    f'Cluster has {total_ngs} node groups — consider consolidating. '
                    f'Many node groups with few nodes degrades CA scalability.')
            return self._create_check_result('C6', True, [],
                f'Cluster has {total_ngs} node groups — within recommended range')
        except Exception as e:
            return self._create_check_error_result('C6', str(e))

    async def _check_managed_node_groups(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C7: Use EKS Managed Node Groups.
        
        Managed Node Groups provide automatic discovery and graceful termination.
        """
        try:
            shared_data = clients.get('shared_data', {})
            managed = shared_data.get('managed_count', 0)
            self_managed = shared_data.get('self_managed_count', 0)
            
            if self_managed > 0:
                return self._create_check_result('C7', False,
                    [f'{self_managed} self-managed ASG(s)'],
                    f'Found {self_managed} self-managed node groups alongside {managed} managed. '
                    f'EKS Managed Node Groups are recommended for automatic discovery and graceful termination.')
            return self._create_check_result('C7', True, [],
                f'All {managed} node groups are EKS Managed')
        except Exception as e:
            return self._create_check_error_result('C7', str(e))

    async def _check_spot_diversification(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C8: Spot instances with proper diversification."""
        try:
            shared_data = clients.get('shared_data', {})
            all_ngs = shared_data.get('all_node_groups', [])

            spot_issues = []
            spot_count = 0
            for ng in all_ngs:
                cap_type = ng.get('capacity_type', '')
                if cap_type == 'SPOT':
                    spot_count += 1
                    instance_types = ng.get('instance_types', [])
                    ng_type = 'Managed' if ng.get('type') == 'managed' else 'Self-managed'
                    if len(instance_types) < 3:
                        spot_issues.append(
                            f'{ng_type}: {ng.get("name")} (only {len(instance_types)} type(s): {", ".join(instance_types) if instance_types else "unknown"})')

            if spot_count == 0:
                return self._create_check_result('C8', True, [],
                    'No Spot instance node groups found - check not applicable')

            if spot_issues:
                return self._create_check_result('C8', False, spot_issues,
                    f'Found {len(spot_issues)} Spot node groups with insufficient instance type diversity. '
                    f'Use 3+ instance types per Spot node group to maximize capacity pool diversity.')
            return self._create_check_result('C8', True, [],
                f'All {spot_count} Spot node groups have sufficient instance type diversity')
        except Exception as e:
            return self._create_check_error_result('C8', str(e))


    async def _check_capacity_separation(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C9: Separate On-Demand and Spot into different ASGs."""
        try:
            shared_data = clients.get('shared_data', {})
            all_ngs = shared_data.get('all_node_groups', [])
            
            mixed_asgs = []
            has_spot = False
            has_on_demand = False
            
            for ng in all_ngs:
                cap = ng.get('capacity_type', 'ON_DEMAND')
                if cap == 'SPOT':
                    has_spot = True
                else:
                    has_on_demand = True
                
                # For self-managed ASGs, check if MixedInstancesPolicy mixes Spot and On-Demand
                if ng.get('type') == 'self_managed':
                    details = ng.get('details', {})
                    mip = details.get('MixedInstancesPolicy', {})
                    if mip:
                        dist = mip.get('InstancesDistribution', {})
                        on_demand_pct = dist.get('OnDemandPercentageAboveBaseCapacity', 100)
                        on_demand_base = dist.get('OnDemandBaseCapacity', 0)
                        # If both On-Demand base > 0 and Spot percentage > 0, it's mixed
                        if 0 < on_demand_pct < 100:
                            mixed_asgs.append(f'Self-managed: {ng.get("name")} (OnDemand: {on_demand_pct}%, Spot: {100-on_demand_pct}%)')
            
            if mixed_asgs:
                return self._create_check_result('C9', False, mixed_asgs,
                    f'Found {len(mixed_asgs)} ASGs mixing On-Demand and Spot capacity. '
                    f'Separate them into different ASGs due to different preemption behavior.')
            
            if not has_spot:
                return self._create_check_result('C9', True, [],
                    'No Spot node groups found - capacity separation not applicable')
            
            return self._create_check_result('C9', True, [],
                'On-Demand and Spot capacity are properly separated into different node groups')
        except Exception as e:
            return self._create_check_error_result('C9', str(e))

    async def _check_expander_strategy(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C10: Configure appropriate expander strategy.
        
        The --expander flag determines which node group to scale.
        least-waste is recommended for cost optimization.
        """
        try:
            shared_data = clients.get('shared_data', {})
            ca_config = shared_data.get('ca_config', {})
            expander = ca_config.get('expander_strategy')
            total_ngs = shared_data.get('total_node_groups', 0)
            
            if total_ngs <= 1:
                return self._create_check_result('C10', True, [],
                    'Single node group — expander strategy not critical')
            
            if not expander:
                return self._create_check_result('C10', False,
                    ['--expander not configured'],
                    f'No --expander strategy configured (defaults to "random"). '
                    f'With {total_ngs} node groups, consider --expander=least-waste for cost optimization '
                    f'or --expander=priority for workload-specific scaling.')
            
            return self._create_check_result('C10', True, [f'expander={expander}'],
                f'Expander strategy configured: {expander}')
        except Exception as e:
            return self._create_check_error_result('C10', str(e))

    def _parse_memory_mi(self, mem_str: str) -> int:
        """Parse K8s memory string to MiB. Returns 0 if unparseable."""
        if not mem_str:
            return 0
        mem_str = str(mem_str).strip()
        try:
            if mem_str.endswith('Gi'):
                return int(float(mem_str[:-2]) * 1024)
            elif mem_str.endswith('Mi'):
                return int(float(mem_str[:-2]))
            elif mem_str.endswith('Ki'):
                return int(float(mem_str[:-2]) / 1024)
            elif mem_str.endswith('G'):
                return int(float(mem_str[:-1]) * 1000)
            elif mem_str.endswith('M'):
                return int(float(mem_str[:-1]))
            elif mem_str.isdigit():
                return int(int(mem_str) / (1024 * 1024))  # bytes to MiB
        except (ValueError, TypeError):
            pass
        return 0

    async def _check_resource_allocation(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C11: Appropriate resource allocation for Cluster Autoscaler.
        
        CA stores all pods and nodes in memory. Resource needs scale with cluster size.
        Recommended minimums (community-observed):
          < 100 nodes: 256Mi memory
          100-500 nodes: 512Mi-1Gi memory
          500-1000 nodes: 1-2Gi memory
          1000+ nodes: 2-4Gi memory (consider VPA or Addon Resizer)
        """
        try:
            shared_data = clients.get('shared_data', {})
            ca_config = shared_data.get('ca_config', {})
            requests = ca_config.get('resource_requests', {})
            limits = ca_config.get('resource_limits', {})
            issues = []
            
            # Basic check: are requests/limits set at all?
            if not requests.get('cpu') and not requests.get('memory'):
                issues.append('No resource requests configured')
            if not limits.get('cpu') and not limits.get('memory'):
                issues.append('No resource limits configured')
            
            # Size-based check: is memory sufficient for the cluster?
            node_count = shared_data.get('node_count', 0)
            
            mem_request_mi = self._parse_memory_mi(requests.get('memory', ''))
            mem_limit_mi = self._parse_memory_mi(limits.get('memory', ''))
            effective_mem = max(mem_request_mi, mem_limit_mi)
            
            # Recommended minimums based on node count
            if node_count > 0 and effective_mem > 0:
                if node_count >= 1000 and effective_mem < 2048:
                    issues.append(
                        f'Cluster has {node_count} nodes but CA memory is only {effective_mem}Mi. '
                        f'Recommend >= 2Gi for 1000+ node clusters. Consider using VPA or Addon Resizer.')
                elif node_count >= 500 and effective_mem < 1024:
                    issues.append(
                        f'Cluster has {node_count} nodes but CA memory is only {effective_mem}Mi. '
                        f'Recommend >= 1Gi for 500+ node clusters.')
                elif node_count >= 100 and effective_mem < 512:
                    issues.append(
                        f'Cluster has {node_count} nodes but CA memory is only {effective_mem}Mi. '
                        f'Recommend >= 512Mi for 100+ node clusters.')
            
            if issues:
                return self._create_check_result('C11', False, issues,
                    f'CA resource allocation issues: {"; ".join(issues)}')
            
            details = (f'Requests: cpu={requests.get("cpu", "N/A")}, memory={requests.get("memory", "N/A")}; '
                       f'Limits: cpu={limits.get("cpu", "N/A")}, memory={limits.get("memory", "N/A")}')
            if node_count > 0:
                details += f' (cluster: {node_count} nodes, {effective_mem}Mi effective memory)'
            return self._create_check_result('C11', True, [], f'CA resource allocation adequate: {details}')
        except Exception as e:
            return self._create_check_error_result('C11', str(e))

    async def _check_scan_interval(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C12: Scan interval and scale-down tuning.
        
        Default 10s scan interval may cause API rate limiting on large clusters.
        Scale-down tuning flags should be configured for production.
        """
        try:
            shared_data = clients.get('shared_data', {})
            ca_config = shared_data.get('ca_config', {})
            scan_interval = ca_config.get('scan_interval')
            scale_down_delay = ca_config.get('scale_down_delay_after_add')
            scale_down_unneeded = ca_config.get('scale_down_unneeded_time')
            command_args = ca_config.get('command_args', [])
            
            # Check for important tuning flags
            all_args_str = ' '.join(str(a) for a in command_args)
            recommendations = []
            
            if not scan_interval:
                recommendations.append('--scan-interval not set (default 10s)')
            
            if not scale_down_delay:
                recommendations.append('--scale-down-delay-after-add not set (default 10m)')
            
            if not scale_down_unneeded:
                recommendations.append('--scale-down-unneeded-time not set (default 10m)')
            
            if '--skip-nodes-with-system-pods' not in all_args_str:
                recommendations.append('--skip-nodes-with-system-pods not configured')
            
            # Note: --balance-similar-node-groups is only needed for multi-AZ EBS workloads
            # Not flagged as a failure - just informational
            
            if recommendations:
                return self._create_check_result('C12', False, recommendations,
                    f'Found {len(recommendations)} CA tuning recommendations. '
                    f'Review scale-down and scan interval settings for your cluster size.')
            
            return self._create_check_result('C12', True, [],
                f'CA tuning flags configured: scan-interval={scan_interval}, '
                f'scale-down-delay={scale_down_delay}, scale-down-unneeded={scale_down_unneeded}')
        except Exception as e:
            return self._create_check_error_result('C12', str(e))

    async def _check_overprovisioning(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C13: Overprovisioning for reduced scheduling latency.
        
        Overprovisioning uses pause pods with negative priority to reserve capacity.
        """
        try:
            k8s_client = clients.get('k8s')
            
            # Check for PriorityClasses with negative value (overprovisioning pattern)
            overprovisioning_found = False
            try:
                pcs = k8s_client.list_resources(
                    kind='PriorityClass', api_version='scheduling.k8s.io/v1'
                )
                if hasattr(pcs, 'items'):
                    for pc in pcs.items:
                        pc_dict = pc.to_dict() if hasattr(pc, 'to_dict') else pc
                        value = pc_dict.get('value', 0)
                        if value is not None and value < 0:
                            overprovisioning_found = True
                            break
            except Exception:
                pass
            
            if overprovisioning_found:
                return self._create_check_result('C13', True, [],
                    'Overprovisioning detected — negative-priority PriorityClass found')
            
            return self._create_check_result('C13', False, [],
                'No overprovisioning configured. Consider using pause pods with negative priority '
                'to reduce pod scheduling latency during scale-up events.')
        except Exception as e:
            return self._create_check_error_result('C13', str(e))

    async def _check_workload_protection(self, clients: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check C14: Protect expensive workloads from scale-down eviction.
        
        Long-running workloads should have the safe-to-evict=false annotation.
        This is informational — checks if the annotation is used anywhere.
        """
        try:
            k8s_client = clients.get('k8s')
            
            # Check if any pods use the safe-to-evict annotation
            protected_count = 0
            try:
                pods = k8s_client.list_resources(kind='Pod', api_version='v1',
                    field_selector='status.phase!=Succeeded,status.phase!=Failed')
                if hasattr(pods, 'items'):
                    for pod in pods.items:
                        pod_dict = pod.to_dict() if hasattr(pod, 'to_dict') else pod
                        annotations = pod_dict.get('metadata', {}).get('annotations', {})
                        if annotations and annotations.get('cluster-autoscaler.kubernetes.io/safe-to-evict') == 'false':
                            protected_count += 1
            except Exception:
                pass
            
            if protected_count > 0:
                return self._create_check_result('C14', True,
                    [f'{protected_count} pod(s) protected'],
                    f'Found {protected_count} pods with safe-to-evict=false annotation')
            
            return self._create_check_result('C14', True, [],
                'No pods use safe-to-evict=false annotation. This is informational - '
                'consider adding it to expensive or long-running workloads if needed.')
        except Exception as e:
            return self._create_check_error_result('C14', str(e))

    async def _check_for_karpenter(self, k8s_client, namespace: Optional[str]) -> bool:
        """Check if Karpenter is deployed in the cluster."""
        try:
            deployments = k8s_client.list_resources(
                kind='Deployment',
                api_version='apps/v1',
                namespace=namespace or 'karpenter'
            )
            
            for deployment in deployments.items:
                if 'karpenter' in deployment.metadata.name.lower():
                    logger.info(f'Found Karpenter deployment: {deployment.metadata.name}')
                    return True
            
            return False
        except Exception as e:
            logger.warning(f'Error checking for Karpenter: {str(e)}')
            return False

    async def _check_for_auto_mode(self, eks_client, cluster_name: str, shared_data: Optional[Dict[str, Any]] = None) -> bool:
        """Check if EKS Auto Mode is enabled for the cluster (optimization #1: reuse cluster_info)."""
        try:
            # Reuse cluster_info if available (optimization #1)
            if shared_data and shared_data.get('cluster_info'):
                cluster_info = shared_data['cluster_info']
                logger.info('Reusing cached cluster info for Auto Mode check')
            else:
                response = eks_client.describe_cluster(name=cluster_name)
                cluster_info = response.get('cluster', {})
            
            # Check if Auto Mode is enabled
            compute_config = cluster_info.get('computeConfig', {})
            enabled = compute_config.get('enabled', False)
            
            if enabled:
                logger.info('EKS Auto Mode is enabled for this cluster')
            
            return enabled
        except Exception as e:
            logger.warning(f'Error checking for Auto Mode: {str(e)}')
            return False
