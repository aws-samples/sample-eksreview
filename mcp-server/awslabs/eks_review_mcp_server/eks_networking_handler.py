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

"""Handler for EKS networking checks in the EKS MCP Server."""

import json
from pathlib import Path
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_response
from awslabs.eks_review_mcp_server.logging_helper import LogLevel, log_with_request_id
from awslabs.eks_review_mcp_server.models import NetworkingCheckResponse
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, Optional, List


class EKSNetworkingHandler:
    """Handler for EKS networking checks in the EKS MCP Server."""

    def __init__(self, mcp, client_cache):
        """Initialize the EKS networking handler.

        Args:
            mcp: The MCP server instance
            client_cache: K8sClientCache instance to share between handlers
        """
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the comprehensive check tool
        self.mcp.tool(name='check_eks_networking')(self.check_eks_networking)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_networking_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load networking check registry: {e}")
            return {}

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Get all checks flattened into a single dictionary."""
        all_checks = {}
        for category in ['networking_checks']:
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

    def _create_check_result(self, check_id: str, compliant: bool, impacted_resources: List[str], details: Any) -> Dict[str, Any]:
        """Create a standardized check result."""
        check_info = self._get_check_info(check_id)
        
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

    def _create_error_response(self, cluster_name: str, error_msg: str) -> NetworkingCheckResponse:
        """Create an error response."""
        return NetworkingCheckResponse(
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

    async def check_eks_networking(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for networking best practices.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
    ) -> NetworkingCheckResponse:
        """Check EKS cluster for networking best practices.

        This tool runs networking-focused checks against your EKS cluster
        to identify potential security and connectivity issues and provides
        remediation guidance.

        The tool evaluates networking best practices including:
        - Cluster endpoint access control and CIDR restrictions
        - Multi-AZ node distribution for high availability
        - VPC configuration and security group settings
        """
        try:
            logger.info(f'Starting networking check for cluster: {cluster_name}')

            # Pre-initialize clients for efficiency
            clients = await self._initialize_clients(cluster_name, region)
            if not clients:
                return self._create_error_response(cluster_name, "Failed to initialize required clients")

            # Get cluster info once for sharing between checks.
            # If describe_cluster fails (e.g. the cluster does not exist or
            # the identity lacks access), there is nothing meaningful to
            # check — every networking check depends on the cluster's VPC
            # config. Fail fast with an error response so callers see the
            # real cause, consistent with the other domain handlers, rather
            # than running all checks against empty data and reporting a
            # misleading "passed" result.
            cluster_info = await self._get_cluster_info(clients['eks'], cluster_name)
            if not cluster_info:
                return self._create_error_response(
                    cluster_name,
                    "Failed to get cluster credentials: could not describe cluster "
                    f"'{cluster_name}'. Verify the cluster name and region, and that "
                    "your identity has eks:DescribeCluster permission.",
                )
            clients['cluster_info'] = cluster_info
            logger.info(f'Retrieved cluster VPC info: {cluster_info.get("vpc_id", "unknown")}')

            # Fetch nodes once for sharing between N2 and N3
            if clients.get('k8s'):
                try:
                    nodes_response = clients['k8s'].list_resources(kind='Node', api_version='v1')
                    clients['nodes'] = nodes_response
                    logger.info(f'Fetched nodes once for sharing between checks')
                except Exception as e:
                    logger.warning(f'Failed to pre-fetch nodes: {str(e)}')
                    clients['nodes'] = None
            else:
                clients['nodes'] = None

            # Run all checks
            check_results = []
            all_compliant = True
            
            # Get all checks and sort by ID for consistent execution order
            all_checks = self._get_all_checks()
            cluster_info = clients.get('cluster_info', {})
            is_auto_mode = cluster_info.get('is_auto_mode', False)
            
            # Store check results by ID for dependency resolution
            check_results_by_id = {}
            
            for check_id in sorted(all_checks.keys()):
                try:
                    check_config = all_checks[check_id]
                    
                    # Check if this check is enabled
                    if not check_config.get('enabled', True):
                        logger.info(f'Skipping disabled check {check_id}')
                        continue
                    
                    # Skip checks that should be skipped for Auto Mode clusters
                    if is_auto_mode and check_config.get('skip_for_auto_mode', False):
                        logger.info(f'Skipping check {check_id} for EKS Auto Mode cluster')
                        # Create a skipped result
                        skipped_result = {
                            'check_name': check_config.get('name', f'Check {check_id}'),
                            'compliant': True,
                            'impacted_resources': [],
                            'details': f'Skipped: not applicable to EKS Auto Mode clusters',
                        }
                        check_results.append(skipped_result)
                        continue
                    
                    logger.info(f'Running networking check {check_id}')
                    result = await self._execute_check(check_id, cluster_name, region, clients, check_results_by_id)
                    check_results.append(result)
                    check_results_by_id[check_id] = result
                    
                    if not result['compliant']:
                        all_compliant = False
                        
                    logger.info(f'Networking check {check_id} completed: {result["compliant"]}')
                    
                except Exception as e:
                    logger.error(f'Error in networking check {check_id}: {str(e)}')
                    error_result = self._create_check_error_result(check_id, str(e))
                    check_results.append(error_result)
                    check_results_by_id[check_id] = error_result
                    all_compliant = False

            # Generate consolidated summary
            summary_data = self._generate_consolidated_summary(cluster_name, check_results)
            summary = summary_data['summary']

            # Create detailed response with JSON data in content
            passed_count = sum(1 for r in check_results if r['compliant'])
            failed_count = len(check_results) - passed_count
            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return NetworkingCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in networking check: {str(e)}')
            return self._create_error_response(cluster_name, str(e))

    def _generate_consolidated_summary(self, cluster_name: str, check_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate a consolidated summary of networking check results."""
        passed_count = sum(1 for r in check_results if r['compliant'])
        failed_count = len(check_results) - passed_count
        total_count = len(check_results)
        
        # Categorize issues by severity
        high_severity_issues = []
        medium_severity_issues = []
        
        for result in check_results:
            if not result['compliant']:
                severity = result.get('severity', 'medium')
                issue_summary = f"{result['check_name']}: {result.get('details', 'Configuration issue detected')}"

                if severity.lower() == 'high':
                    high_severity_issues.append(issue_summary)
                else:
                    medium_severity_issues.append(issue_summary)
        
        # Build comprehensive summary
        summary_parts = [
            f'Cluster {cluster_name} networking assessment: {passed_count}/{total_count} checks passed'
        ]
        
        if high_severity_issues:
            summary_parts.append(f'High priority issues ({len(high_severity_issues)}): {"; ".join(high_severity_issues[:2])}{"..." if len(high_severity_issues) > 2 else ""}')
        
        if medium_severity_issues:
            summary_parts.append(f'Medium priority issues ({len(medium_severity_issues)}): {"; ".join(medium_severity_issues[:1])}{"..." if len(medium_severity_issues) > 1 else ""}')
        
        if passed_count == total_count:
            summary_parts.append('All networking checks passed - cluster follows networking best practices')
        
        return {
            'summary': '. '.join(summary_parts),
            'passed_count': passed_count,
            'failed_count': failed_count,
            'total_count': total_count,
            'high_severity_issues': len(high_severity_issues),
            'medium_severity_issues': len(medium_severity_issues)
        }

    async def _initialize_clients(self, cluster_name: str, region: Optional[str]) -> Optional[Dict[str, Any]]:
        """Initialize all required clients for networking checks."""
        try:
            clients = {}
            
            # Initialize AWS EKS client (needed for N1)
            try:
                clients['eks'] = AwsHelper.create_boto3_client('eks', region_name=region)
                logger.info('Successfully initialized EKS client')
            except Exception as e:
                logger.error(f'Failed to initialize EKS client: {str(e)}')
                return None
            
            # Initialize AWS EC2 client (needed for N3)
            try:
                clients['ec2'] = AwsHelper.create_boto3_client('ec2', region_name=region)
                logger.info('Successfully initialized EC2 client')
            except Exception as e:
                logger.error(f'Failed to initialize EC2 client: {str(e)}')
                return None
            
            # Initialize Kubernetes client (needed for N2, N3)
            try:
                clients['k8s'] = self.client_cache.get_client(cluster_name, region=region)
                logger.info('Successfully initialized Kubernetes client')
            except Exception as e:
                logger.warning(f'Failed to initialize Kubernetes client: {str(e)}')
                # K8s client failure is not fatal - some checks may still work
                clients['k8s'] = None
            
            return clients
            
        except Exception as e:
            logger.error(f'Error initializing clients: {str(e)}')
            return None

    async def _get_cluster_info(self, eks_client, cluster_name: str) -> Optional[Dict[str, Any]]:
        """Get cluster information including VPC details and Auto Mode detection."""
        try:
            response = eks_client.describe_cluster(name=cluster_name)
            cluster_info = response['cluster']
            vpc_config = cluster_info.get('resourcesVpcConfig', {})
            
            # Detect EKS Auto Mode
            compute_config = cluster_info.get('computeConfig', {})
            storage_config = cluster_info.get('storageConfig', {})
            kubernetes_network_config = cluster_info.get('kubernetesNetworkConfig', {})
            elastic_load_balancing = kubernetes_network_config.get('elasticLoadBalancing', {})
            
            # Check if any Auto Mode features are enabled
            is_auto_mode = (
                compute_config.get('enabled', False) or
                storage_config.get('blockStorage', {}).get('enabled', False) or
                elastic_load_balancing.get('enabled', False)
            )
            
            # Extract VPC and subnet information
            cluster_data = {
                'vpc_id': vpc_config.get('vpcId'),
                'subnet_ids': vpc_config.get('subnetIds', []),
                'security_group_ids': vpc_config.get('securityGroupIds', []),
                'cluster_security_group_id': vpc_config.get('clusterSecurityGroupId'),
                'endpoint_config_private_access': vpc_config.get('endpointConfigPrivateAccess', False),
                'endpoint_config_public_access': vpc_config.get('endpointConfigPublicAccess', True),
                'public_access_cidrs': vpc_config.get('publicAccessCidrs', []),
                'is_auto_mode': is_auto_mode,
                'auto_mode_features': {
                    'compute_enabled': compute_config.get('enabled', False),
                    'storage_enabled': storage_config.get('blockStorage', {}).get('enabled', False),
                    'elastic_load_balancing_enabled': elastic_load_balancing.get('enabled', False)
                }
            }
            
            cluster_type = 'EKS Auto Mode' if is_auto_mode else 'Standard EKS'
            logger.info(f'Cluster {cluster_name} ({cluster_type}) VPC: {cluster_data["vpc_id"]}, Subnets: {len(cluster_data["subnet_ids"])}')
            
            if is_auto_mode:
                enabled_features = []
                if compute_config.get('enabled', False):
                    enabled_features.append('compute')
                if storage_config.get('blockStorage', {}).get('enabled', False):
                    enabled_features.append('storage')
                if elastic_load_balancing.get('enabled', False):
                    enabled_features.append('elastic-load-balancing')
                logger.info(f'Auto Mode features enabled: {", ".join(enabled_features)}')
            
            return cluster_data
            
        except Exception as e:
            logger.warning(f'Failed to get cluster info for optimization: {str(e)}')
            return None

    async def _execute_check(self, check_id: str, cluster_name: str, region: Optional[str], clients: Dict[str, Any], previous_results: Dict[str, Any] = None) -> Dict[str, Any]:
        """Execute a single check based on its ID."""
        
        # Map check IDs to their corresponding methods
        check_methods = {
            'N1': self._check_cluster_endpoint_access,
            'N2': self._check_multi_az_node_distribution,
            'N3': self._check_private_subnet_deployment,
            'N4': self._check_vpc_cni_managed_addon,
            'N5': self._check_vpc_cni_service_account,
            'N6': self._check_subnet_ip_availability,
            'N7': self._check_load_balancer_target_type,
            'N8': self._check_prestop_hooks_for_ingress,
            'N9': self._check_kube_proxy_mode_for_scale,
        }
        
        check_method = check_methods.get(check_id)
        if not check_method:
            return self._create_check_error_result(check_id, f"No implementation found for check {check_id}")
        
        # Check client requirements and execute
        if check_id == 'N1':
            # N1 requires EKS client
            if not clients.get('eks'):
                return self._create_check_error_result(check_id, "EKS client not available")
            return await check_method(cluster_name, region, clients['eks'], clients.get('cluster_info'))
            
        elif check_id == 'N2':
            # N2 requires Kubernetes client
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to analyze node distribution")
            return await check_method(cluster_name, region, clients['k8s'], clients.get('nodes'))
            
        elif check_id == 'N3':
            # N3 requires both Kubernetes and EC2 clients
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to analyze node subnets")
            if not clients.get('ec2'):
                return self._create_check_error_result(check_id, "EC2 client not available - unable to analyze subnet types")
            return await check_method(cluster_name, region, clients['k8s'], clients['ec2'], clients.get('cluster_info'), clients.get('nodes'))
            
        elif check_id == 'N4':
            # N4 requires Kubernetes client to check VPC CNI daemonset
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to analyze VPC CNI configuration")
            return await check_method(cluster_name, region, clients['k8s'])
            
        elif check_id == 'N5':
            # N5 requires both Kubernetes and EKS clients, and depends on N4 results
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to analyze VPC CNI service account")
            if not clients.get('eks'):
                return self._create_check_error_result(check_id, "EKS client not available - unable to check managed add-on")
            
            # Get N4 result for dependency
            n4_result = previous_results.get('N4') if previous_results else None
            return await check_method(cluster_name, region, clients['k8s'], clients['eks'], n4_result)
            
        elif check_id == 'N6':
            # N6 reuses subnet data from N3 and VPC CNI version from N4/N5, plus queries VPC CNI env vars
            n3_result = previous_results.get('N3') if previous_results else None
            n4_result = previous_results.get('N4') if previous_results else None
            n5_result = previous_results.get('N5') if previous_results else None
            return await check_method(cluster_name, region, n3_result, n4_result, n5_result, clients.get('k8s'))
            
        elif check_id == 'N7':
            # N7 requires Kubernetes client to check Ingress and Service resources
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to analyze load balancer target types")
            return await check_method(cluster_name, region, clients['k8s'])
            
        elif check_id == 'N8':
            # N8 requires Kubernetes client and reuses Ingress data from N7
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to check PreStop hooks")
            n7_result = previous_results.get('N7') if previous_results else None
            return await check_method(cluster_name, region, clients['k8s'], n7_result)
            
        elif check_id == 'N9':
            # N9 requires Kubernetes client to check kube-proxy mode and count services
            if not clients.get('k8s'):
                return self._create_check_error_result(check_id, "Kubernetes client not available - unable to check kube-proxy mode")
            return await check_method(cluster_name, region, clients['k8s'])
            
        else:
            # Generic execution for future checks
            return await check_method(cluster_name, region, clients)

    async def _check_cluster_endpoint_access(self, cluster_name: str, region: Optional[str], eks_client, cluster_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Check N1: EKS Cluster Endpoint Access Control."""
        try:
            logger.info(f'Checking cluster endpoint access for cluster: {cluster_name}')
            
            # Use shared cluster info if available, otherwise make API call
            if cluster_info:
                endpoint_config_private_access = cluster_info.get('endpoint_config_private_access', False)
                endpoint_config_public_access = cluster_info.get('endpoint_config_public_access', True)
                public_access_cidrs = cluster_info.get('public_access_cidrs', [])
                logger.info('Using shared cluster info for N1 check')
            else:
                # Fallback to API call if shared info not available
                response = eks_client.describe_cluster(name=cluster_name)
                cluster_data = response['cluster']
                vpc_config = cluster_data.get('resourcesVpcConfig', {})
                endpoint_config_private_access = vpc_config.get('endpointConfigPrivateAccess', False)
                endpoint_config_public_access = vpc_config.get('endpointConfigPublicAccess', True)
                public_access_cidrs = vpc_config.get('publicAccessCidrs', [])
                logger.info('Made individual API call for N1 check')
            
            logger.info(f'Cluster endpoint configuration - Private: {endpoint_config_private_access}, Public: {endpoint_config_public_access}, CIDRs: {public_access_cidrs}')
            
            # Determine compliance
            impacted_resources = []
            
            # Check if public access is enabled with unrestricted access
            has_unrestricted_public_access = False
            if endpoint_config_public_access:
                if '0.0.0.0/0' in public_access_cidrs or not public_access_cidrs:
                    has_unrestricted_public_access = True
                    impacted_resources.append(f'Cluster: {cluster_name}')
            
            # Determine overall compliance
            is_compliant = not has_unrestricted_public_access
            
            # Build simple details string
            if not endpoint_config_public_access:
                details = 'API server endpoint is private only'
            elif has_unrestricted_public_access:
                details = 'API server endpoint has unrestricted public access (0.0.0.0/0)'
            else:
                cidrs_str = ', '.join(public_access_cidrs) if public_access_cidrs else 'none'
                details = f'API server endpoint has restricted public access (CIDRs: {cidrs_str})'
            
            return self._create_check_result('N1', is_compliant, impacted_resources, details)
            
        except Exception as e:
            logger.error(f'Error checking cluster endpoint access: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N1', False, [], f'Failed to check cluster endpoint access: {str(e)}')

    async def _check_multi_az_node_distribution(self, cluster_name: str, region: Optional[str], k8s_client, shared_nodes=None) -> Dict[str, Any]:
        """Check N2: Multi-AZ Node Distribution."""
        try:
            logger.info(f'Checking multi-AZ node distribution for cluster: {cluster_name}')
            
            # Use shared nodes if available, otherwise fetch
            if shared_nodes is not None:
                nodes_response = shared_nodes
                logger.info('Using shared nodes for N2 check')
            else:
                nodes_response = k8s_client.list_resources(kind='Node', api_version='v1')
            
            if not hasattr(nodes_response, 'items') or not nodes_response.items:
                return self._create_check_result('N2', False, [], 'No nodes found in cluster')
            
            # Extract AZ information from nodes
            az_distribution = {}
            total_nodes = 0
            impacted_resources = []
            
            for node in nodes_response.items:
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
                return self._create_check_result('N2', False, impacted_resources, 'No nodes with AZ labels found')
            
            # Check if nodes are distributed across multiple AZs
            num_azs = len(az_distribution)
            if num_azs < 2:
                is_compliant = False
                issues = [f'Nodes are only in {num_azs} availability zone(s). Minimum 2 AZs recommended for high availability.']
                risk_level = 'high'
                for az in az_distribution.keys():
                    impacted_resources.append(f'All nodes in single AZ: {az}')
            else:
                # Check for uneven distribution (more than 30% deviation)
                expected_nodes_per_az = total_nodes / num_azs
                max_deviation = 0
                issues = []
                
                for az, node_count in az_distribution.items():
                    deviation = abs(node_count - expected_nodes_per_az) / expected_nodes_per_az
                    max_deviation = max(max_deviation, deviation)
                    
                    if deviation > 0.3:  # 30% deviation threshold
                        issues.append(f'AZ {az} has {node_count} nodes (expected ~{expected_nodes_per_az:.1f}), deviation: {deviation:.1%}')
                        impacted_resources.append(f'AZ {az}: {node_count} nodes (uneven distribution)')
                
                is_compliant = max_deviation <= 0.3
                risk_level = 'medium' if not is_compliant else 'low'
            
            # Build details string
            az_counts = ', '.join(f'{az}: {count}' for az, count in sorted(az_distribution.items()))
            if num_azs < 2:
                az_name = list(az_distribution.keys())[0]
                details = f'All {total_nodes} nodes in single AZ {az_name}'
            elif is_compliant:
                details = f'Nodes distributed across {num_azs} AZs ({az_counts})'
            else:
                details = f'Uneven node distribution across {num_azs} AZs ({az_counts})'
            
            return self._create_check_result('N2', is_compliant, impacted_resources, details)
            
        except Exception as e:
            logger.error(f'Error checking multi-AZ node distribution: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N2', False, [], f'Failed to check multi-AZ node distribution: {str(e)}')

    async def _check_private_subnet_deployment(self, cluster_name: str, region: Optional[str], k8s_client, ec2_client, cluster_info: Optional[Dict[str, Any]] = None, shared_nodes=None) -> Dict[str, Any]:
        """Check N3: Private Subnet Node Deployment."""
        try:
            logger.info(f'Checking private subnet deployment for cluster: {cluster_name}')
            
            # Use shared nodes if available, otherwise fetch
            if shared_nodes is not None:
                nodes_response = shared_nodes
                logger.info('Using shared nodes for N3 check')
            else:
                nodes_response = k8s_client.list_resources(kind='Node', api_version='v1')
            
            if not hasattr(nodes_response, 'items') or not nodes_response.items:
                return self._create_check_result('N3', False, [], 'No nodes found in cluster')
            
            # Cap nodes at 15 to limit EC2 API calls, but spread across AZs and IP ranges
            MAX_NODES_TO_CHECK = 15
            all_nodes = nodes_response.items
            total_node_count = len(all_nodes)
            
            if total_node_count <= MAX_NODES_TO_CHECK:
                nodes_to_check = all_nodes
                nodes_capped = False
            else:
                # Group nodes by (AZ, IP /24 prefix) to maximise subnet coverage
                from collections import defaultdict
                az_ip_buckets = defaultdict(list)  # key: (az, ip_prefix)
                no_az_nodes = []
                
                for node in all_nodes:
                    try:
                        node_dict = node.to_dict() if hasattr(node, 'to_dict') else node
                        metadata = node_dict.get('metadata', {})
                        labels = metadata.get('labels', {})
                        az = (labels.get('topology.kubernetes.io/zone')
                              or labels.get('failure-domain.beta.kubernetes.io/zone')
                              or '')
                        
                        # Extract InternalIP and derive /24 prefix
                        ip_prefix = ''
                        status = node_dict.get('status', {})
                        for addr in status.get('addresses', []):
                            if addr.get('type') == 'InternalIP':
                                octets = addr.get('address', '').split('.')
                                if len(octets) >= 3:
                                    ip_prefix = '.'.join(octets[:3])
                                break
                        
                        if az:
                            az_ip_buckets[(az, ip_prefix)].append(node)
                        else:
                            no_az_nodes.append(node)
                    except Exception:
                        no_az_nodes.append(node)
                
                # Pick one node per unique (AZ, IP prefix) first, then fill remaining slots
                nodes_to_check = []
                remaining = []
                for key in sorted(az_ip_buckets.keys()):
                    bucket = az_ip_buckets[key]
                    nodes_to_check.append(bucket[0])
                    remaining.extend(bucket[1:])
                
                # If we already have enough unique buckets, cap
                if len(nodes_to_check) > MAX_NODES_TO_CHECK:
                    nodes_to_check = nodes_to_check[:MAX_NODES_TO_CHECK]
                else:
                    # Fill remaining slots from leftover nodes + no-AZ nodes
                    filler = remaining + no_az_nodes
                    space = MAX_NODES_TO_CHECK - len(nodes_to_check)
                    nodes_to_check.extend(filler[:space])
                
                nodes_capped = True
                unique_azs = set(k[0] for k in az_ip_buckets.keys() if k[0])
                unique_prefixes = len(az_ip_buckets)
                logger.info(
                    f'Sampled {len(nodes_to_check)} nodes across {len(unique_azs)} AZs '
                    f'and {unique_prefixes} IP prefixes (of {total_node_count} total)'
                )
            
            # Extract instance IDs from nodes and batch query EC2 for subnet information
            instance_ids_to_query = []
            nodes_without_instance_id = []
            
            # Collect instance IDs from capped nodes
            for node in nodes_to_check:
                try:
                    node_dict = node.to_dict() if hasattr(node, 'to_dict') else node
                    metadata = node_dict.get('metadata', {})
                    node_name = metadata.get('name', 'unknown')
                    
                    # Get instance ID from spec.providerID
                    spec = node_dict.get('spec', {})
                    provider_id = spec.get('providerID', '')
                    
                    if 'aws://' in provider_id:
                        try:
                            # Extract instance ID from provider ID (format: aws:///zone/instance-id)
                            instance_id = provider_id.split('/')[-1]
                            if instance_id and instance_id.startswith('i-'):
                                instance_ids_to_query.append((instance_id, node_name))
                            else:
                                logger.warning(f'Invalid instance ID format for node {node_name}: {instance_id}')
                                nodes_without_instance_id.append(node_name)
                        except Exception as e:
                            logger.warning(f'Failed to parse provider ID for node {node_name}: {str(e)}')
                            nodes_without_instance_id.append(node_name)
                    else:
                        logger.warning(f'Node {node_name} missing AWS provider ID')
                        nodes_without_instance_id.append(node_name)
                        
                except Exception as node_error:
                    logger.error(f'Error processing node: {str(node_error)}')
                    nodes_without_instance_id.append(node_name)
            
            if not instance_ids_to_query:
                impacted_resources = [f'Node: {name} (missing instance ID)' for name in nodes_without_instance_id]
                return self._create_check_result('N3', False, impacted_resources, 'No valid instance IDs found for any nodes')
            
            # Batch query EC2 for instance subnet information
            logger.info(f'Querying EC2 for subnet info of {len(instance_ids_to_query)} instances')
            instance_ids_list = [pair[0] for pair in instance_ids_to_query]
            logger.info(f'Instance IDs to query: {instance_ids_list}')
            instance_subnets = await self._get_instance_subnets_batch(ec2_client, instance_ids_to_query)
            logger.info(f'Retrieved subnet info for {len(instance_subnets)} instances')
            
            # Build subnet mapping
            node_subnets = set()
            nodes_by_subnet = {}
            impacted_resources = []
            
            for instance_id, node_name in instance_ids_to_query:
                subnet_id = instance_subnets.get(instance_id)
                if subnet_id:
                    node_subnets.add(subnet_id)
                    if subnet_id not in nodes_by_subnet:
                        nodes_by_subnet[subnet_id] = []
                    nodes_by_subnet[subnet_id].append(node_name)
                else:
                    logger.warning(f'No subnet found for node {node_name} (instance {instance_id})')
                    impacted_resources.append(f'Node: {node_name} (subnet not found)')
            
            # Add nodes without instance IDs to impacted resources
            for node_name in nodes_without_instance_id:
                impacted_resources.append(f'Node: {node_name} (missing instance ID)')
            
            if not node_subnets:
                return self._create_check_result('N3', False, impacted_resources, 'No subnet information found for any nodes')
            
            logger.info(f'Found nodes in subnets: {list(node_subnets)}')
            
            # Analyze subnet types using EC2 API with VPC optimization
            cluster_vpc_id = cluster_info.get('vpc_id') if cluster_info else None
            subnet_analysis = await self._analyze_subnet_types(ec2_client, list(node_subnets), cluster_vpc_id)
            
            # Determine compliance
            public_subnets = []
            private_subnets = []
            unknown_subnets = []
            
            for subnet_id, subnet_info in subnet_analysis.items():
                if subnet_info['type'] == 'public':
                    public_subnets.append(subnet_id)
                    for node in nodes_by_subnet.get(subnet_id, []):
                        impacted_resources.append(f'Node: {node} (in public subnet {subnet_id})')
                elif subnet_info['type'] == 'private':
                    private_subnets.append(subnet_id)
                else:
                    unknown_subnets.append(subnet_id)
                    for node in nodes_by_subnet.get(subnet_id, []):
                        impacted_resources.append(f'Node: {node} (in unknown subnet {subnet_id})')
            
            # Compliance: all nodes should be in private subnets
            is_compliant = len(public_subnets) == 0 and len(unknown_subnets) == 0
            
            issues = []
            if public_subnets:
                issues.append(f'{len(public_subnets)} public subnet(s) detected with worker nodes')
            if unknown_subnets:
                issues.append(f'{len(unknown_subnets)} subnet(s) with unknown type detected')
            if nodes_capped:
                issues.append(f'Only checked {MAX_NODES_TO_CHECK} of {total_node_count} nodes (capped)')
            
            risk_level = 'high' if public_subnets else ('medium' if unknown_subnets else 'low')
            
            # Build details string
            nodes_checked_count = len(nodes_to_check)
            if public_subnets:
                nodes_in_public = sum(len(nodes_by_subnet.get(s, [])) for s in public_subnets)
                details = f'Found {nodes_in_public} nodes in {len(public_subnets)} public subnets'
            else:
                details = 'All nodes deployed in private subnets'
            if nodes_capped:
                details += f' (sampled {nodes_checked_count} of {total_node_count} nodes)'
            
            # Store subnet_analysis in the result for N6 to consume
            result = self._create_check_result('N3', is_compliant, impacted_resources, details)
            result['_subnet_analysis'] = {
                'subnet_details': subnet_analysis,
                'nodes_by_subnet': nodes_by_subnet
            }
            return result
            
        except Exception as e:
            logger.error(f'Error checking private subnet deployment: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N3', False, [], f'Failed to check private subnet deployment: {str(e)}')

    async def _get_instance_subnets_batch(self, ec2_client, instance_node_pairs: List[tuple]) -> Dict[str, str]:
        """Get subnet IDs for multiple EC2 instances in batch."""
        instance_subnets = {}
        
        if not instance_node_pairs:
            return instance_subnets
        
        try:
            # Extract instance IDs for the API call
            instance_ids = [pair[0] for pair in instance_node_pairs]
            logger.info(f'Querying EC2 for instance subnet info: {instance_ids}')
            
            # Batch query EC2 for all instances at once
            response = ec2_client.describe_instances(InstanceIds=instance_ids)
            
            for reservation in response['Reservations']:
                for instance in reservation['Instances']:
                    instance_id = instance['InstanceId']
                    subnet_id = instance.get('SubnetId')
                    if subnet_id:
                        instance_subnets[instance_id] = subnet_id
                        logger.debug(f'Instance {instance_id} -> Subnet {subnet_id}')
                    else:
                        logger.warning(f'Instance {instance_id} has no subnet ID')
                        
        except Exception as e:
            logger.error(f'Failed to get subnets for instances in batch: {str(e)}')
        
        return instance_subnets

    async def _analyze_subnet_types(self, ec2_client, subnet_ids: List[str], cluster_vpc_id: Optional[str] = None) -> Dict[str, Dict[str, Any]]:
        """Analyze subnet types (public/private) using EC2 API with VPC filtering optimization."""
        subnet_analysis = {}
        
        try:
            # Get subnet details
            response = ec2_client.describe_subnets(SubnetIds=subnet_ids)
            
            # Get VPC IDs from subnets (for filtering route tables)
            if cluster_vpc_id:
                # Use cluster VPC ID for optimization
                vpc_ids = [cluster_vpc_id]
                logger.info(f'Using cluster VPC ID for route table filtering: {cluster_vpc_id}')
            else:
                # Fallback: extract VPC IDs from subnets
                vpc_ids = list(set(subnet['VpcId'] for subnet in response['Subnets']))
                logger.info(f'Extracted VPC IDs from subnets: {vpc_ids}')
            
            # Get route tables ONLY for relevant VPCs (major optimization!)
            if vpc_ids:
                route_tables_response = ec2_client.describe_route_tables(
                    Filters=[{'Name': 'vpc-id', 'Values': vpc_ids}]
                )
                logger.info(f'Retrieved {len(route_tables_response["RouteTables"])} route tables for {len(vpc_ids)} VPCs')
            else:
                # Fallback to all route tables if no VPC IDs found
                route_tables_response = ec2_client.describe_route_tables()
                logger.warning('No VPC IDs found, querying all route tables')
            
            route_tables = route_tables_response['RouteTables']
            
            for subnet in response['Subnets']:
                subnet_id = subnet['SubnetId']
                vpc_id = subnet['VpcId']
                az = subnet['AvailabilityZone']
                
                # Determine if subnet is public or private by checking route tables
                is_public = self._is_subnet_public(subnet_id, vpc_id, route_tables)
                
                subnet_analysis[subnet_id] = {
                    'type': 'public' if is_public else 'private',
                    'vpc_id': vpc_id,
                    'availability_zone': az,
                    'cidr_block': subnet.get('CidrBlock', 'unknown'),
                    'available_ip_address_count': subnet.get('AvailableIpAddressCount', 0),
                    'total_ip_addresses': self._calculate_total_ips(subnet.get('CidrBlock', '')),
                    'ip_utilization_percent': self._calculate_ip_utilization(
                        subnet.get('AvailableIpAddressCount', 0),
                        subnet.get('CidrBlock', '')
                    )
                }
                
        except Exception as e:
            logger.error(f'Error analyzing subnet types: {str(e)}')
            # Mark all subnets as unknown if we can't analyze them
            for subnet_id in subnet_ids:
                subnet_analysis[subnet_id] = {
                    'type': 'unknown',
                    'vpc_id': 'unknown',
                    'availability_zone': 'unknown',
                    'cidr_block': 'unknown'
                }
        
        return subnet_analysis

    def _is_subnet_public(self, subnet_id: str, vpc_id: str, route_tables: List[Dict[str, Any]]) -> bool:
        """Determine if a subnet is public by checking its route table for internet gateway routes."""
        try:
            # Find the route table associated with this subnet
            subnet_route_table = None
            
            # First, look for explicit subnet associations
            for rt in route_tables:
                if rt['VpcId'] == vpc_id:
                    for association in rt.get('Associations', []):
                        if association.get('SubnetId') == subnet_id:
                            subnet_route_table = rt
                            break
                    if subnet_route_table:
                        break
            
            # If no explicit association, use the main route table for the VPC
            if not subnet_route_table:
                for rt in route_tables:
                    if rt['VpcId'] == vpc_id:
                        for association in rt.get('Associations', []):
                            if association.get('Main', False):
                                subnet_route_table = rt
                                break
                        if subnet_route_table:
                            break
            
            if not subnet_route_table:
                logger.warning(f'No route table found for subnet {subnet_id}')
                return False
            
            # Check if route table has a route to an internet gateway
            for route in subnet_route_table.get('Routes', []):
                gateway_id = route.get('GatewayId', '')
                if gateway_id.startswith('igw-'):
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f'Error checking if subnet {subnet_id} is public: {str(e)}')
            return False

    async def _check_vpc_cni_managed_addon(self, cluster_name: str, region: Optional[str], k8s_client) -> Dict[str, Any]:
        """Check N4: VPC CNI Managed Add-on Usage."""
        try:
            logger.info(f'Checking VPC CNI managed add-on usage for cluster: {cluster_name}')
            
            # Get the aws-node daemonset with managed fields
            try:
                daemonset_response = k8s_client.list_resources(
                    kind='DaemonSet', 
                    api_version='apps/v1',
                    namespace='kube-system',
                    field_selector='metadata.name=aws-node'
                )
                
                if not hasattr(daemonset_response, 'items') or not daemonset_response.items:
                    # aws-node not found - likely using alternative networking solution
                    return self._create_check_result('N4', True, [], 'aws-node daemonset not found, cluster likely uses alternative networking solution')
                
                # Get the aws-node daemonset
                aws_node_ds = daemonset_response.items[0]
                ds_dict = aws_node_ds.to_dict() if hasattr(aws_node_ds, 'to_dict') else aws_node_ds
                
                # Analyze managed fields to determine if it's managed by EKS
                metadata = ds_dict.get('metadata', {})
                managed_fields = metadata.get('managedFields', [])
                
                # Check for EKS management
                is_eks_managed = False
                eks_managed_fields = []
                other_managers = []
                
                for field in managed_fields:
                    manager = field.get('manager', '')
                    if manager.lower() == 'eks':
                        is_eks_managed = True
                        eks_managed_fields.append(field)
                    else:
                        other_managers.append(manager)
                
                # Get additional daemonset info
                spec = ds_dict.get('spec', {})
                template = spec.get('template', {})
                template_spec = template.get('spec', {})
                containers = template_spec.get('containers', [])
                
                # Find aws-node container
                aws_node_container = None
                for container in containers:
                    if container.get('name') == 'aws-node':
                        aws_node_container = container
                        break
                
                image_info = 'unknown'
                if aws_node_container:
                    image_info = aws_node_container.get('image', 'unknown')
                
                # Determine compliance
                impacted_resources = []
                issues = []
                
                if not is_eks_managed:
                    issues.append('VPC CNI is not managed by EKS - missing managed add-on benefits')
                    impacted_resources.append(f'DaemonSet: kube-system/aws-node (not EKS managed)')
                
                is_compliant = is_eks_managed
                risk_level = 'medium' if not is_compliant else 'low'
                
                # Build details string
                if is_eks_managed:
                    # Extract version from image
                    version_str = ''
                    if image_info and ':v' in image_info:
                        version_str = ' (' + image_info.split(':')[-1] + ')'
                    elif image_info and ':' in image_info:
                        version_str = ' (' + image_info.split(':')[-1] + ')'
                    details = f'VPC CNI is deployed as EKS managed add-on{version_str}'
                else:
                    details = 'VPC CNI is self-managed, not using EKS managed add-on'
                
                # Store vpc_cni_analysis data for N5/N6 to consume
                result = self._create_check_result('N4', is_compliant, impacted_resources, details)
                result['_vpc_cni_analysis'] = {
                    'daemonset_found': True,
                    'is_eks_managed': is_eks_managed,
                    'image': image_info,
                }
                return result
                
            except Exception as k8s_error:
                logger.error(f'Error querying aws-node daemonset: {str(k8s_error)}')
                return self._create_check_result('N4', False, [], f'Failed to query aws-node daemonset: {str(k8s_error)}')
                
        except Exception as e:
            logger.error(f'Error checking VPC CNI managed add-on: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N4', False, [], f'Failed to check VPC CNI managed add-on: {str(e)}')

    async def _check_vpc_cni_service_account(self, cluster_name: str, region: Optional[str], k8s_client, eks_client, n4_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Check N5: VPC CNI Service Account IAM Role."""
        try:
            logger.info(f'Checking VPC CNI service account configuration for cluster: {cluster_name}')
            
            # Check if N4 indicates EKS managed VPC CNI
            is_eks_managed = n4_result and n4_result.get('compliant', False) if n4_result else False
            
            if is_eks_managed:
                logger.info('VPC CNI is EKS managed - using describe-addon to check service account')
                return await self._check_managed_vpc_cni_service_account(cluster_name, region, eks_client)
            else:
                logger.info('VPC CNI is self-managed - using Kubernetes API to check service account')
                return await self._check_self_managed_vpc_cni_service_account(cluster_name, region, k8s_client)
                
        except Exception as e:
            logger.error(f'Error checking VPC CNI service account: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N5', False, [], f'Failed to check VPC CNI service account: {str(e)}')

    async def _check_managed_vpc_cni_service_account(self, cluster_name: str, region: Optional[str], eks_client) -> Dict[str, Any]:
        """Check service account for EKS managed VPC CNI using describe-addon."""
        try:
            response = eks_client.describe_addon(
                clusterName=cluster_name,
                addonName='vpc-cni'
            )
            
            addon_info = response['addon']
            service_account_role_arn = addon_info.get('serviceAccountRoleArn')
            pod_identity_associations = addon_info.get('podIdentityAssociations', [])
            addon_version = addon_info.get('addonVersion', 'unknown')
            addon_status = addon_info.get('status', 'unknown')
            
            impacted_resources = []
            issues = []
            
            # Check for either service account role or pod identity associations
            has_service_account_role = bool(service_account_role_arn)
            has_pod_identity_associations = bool(pod_identity_associations)
            
            if has_service_account_role or has_pod_identity_associations:
                # Compliant - uses dedicated IAM role via either method
                is_compliant = True
                if has_service_account_role:
                    logger.info(f'EKS managed VPC CNI uses service account role: {service_account_role_arn}')
                if has_pod_identity_associations:
                    logger.info(f'EKS managed VPC CNI uses pod identity associations: {pod_identity_associations}')
            else:
                # Non-compliant - no dedicated IAM role configured
                is_compliant = False
                issues.append('EKS managed VPC CNI not configured with dedicated IAM role (neither service account role nor pod identity associations)')
                impacted_resources.append(f'EKS Add-on: vpc-cni (no dedicated IAM role)')
            
            risk_level = 'low' if is_compliant else 'medium'
            
            # Build details string
            if is_compliant:
                method = self._determine_iam_method(has_service_account_role, has_pod_identity_associations)
                details = f'VPC CNI uses dedicated IAM role via {method} ({addon_version})'
            else:
                details = 'VPC CNI using node instance profile instead of dedicated IAM role'
            
            # Store service_account_analysis for N6 to consume
            result = self._create_check_result('N5', is_compliant, impacted_resources, details)
            result['_service_account_analysis'] = {
                'management_type': 'eks_managed',
                'addon_version': addon_version,
            }
            return result
            
        except Exception as e:
            if hasattr(e, 'response') and e.response.get('Error', {}).get('Code') == 'ResourceNotFoundException':
                # Add-on not found - mark as non-compliant
                logger.warning(f'VPC CNI add-on not found for cluster {cluster_name}')
                return self._create_check_result('N5', False, [f'Cluster: {cluster_name} (add-on not found)'], 'VPC CNI add-on not found despite N4 indicating managed status')
            else:
                raise

    async def _check_self_managed_vpc_cni_service_account(self, cluster_name: str, region: Optional[str], k8s_client) -> Dict[str, Any]:
        """Check service account for self-managed VPC CNI using Kubernetes API."""
        try:
            # First, check if aws-node daemonset exists
            try:
                daemonset_response = k8s_client.list_resources(
                    kind='DaemonSet', 
                    api_version='apps/v1',
                    namespace='kube-system',
                    field_selector='metadata.name=aws-node'
                )
                
                if not hasattr(daemonset_response, 'items') or not daemonset_response.items:
                    # aws-node not found - likely using alternative networking solution
                    return self._create_check_result('N5', True, [], 'aws-node daemonset not found, cluster likely uses alternative networking solution')
                
                # Get the aws-node daemonset
                aws_node_ds = daemonset_response.items[0]
                ds_dict = aws_node_ds.to_dict() if hasattr(aws_node_ds, 'to_dict') else aws_node_ds
                
                # Extract service account information
                spec = ds_dict.get('spec', {})
                template = spec.get('template', {})
                template_spec = template.get('spec', {})
                service_account_name = template_spec.get('serviceAccountName', template_spec.get('serviceAccount', ''))
                
                logger.info(f'aws-node daemonset uses service account: {service_account_name or "default"}')
                
                # Simple service account analysis
                impacted_resources = []
                issues = []
                
                # Check service account configuration - simple logic
                if not service_account_name or service_account_name == 'aws-node':
                    # Uses default or aws-node service account - not compliant
                    is_compliant = False
                    issues.append('VPC CNI uses default service account instead of dedicated service account')
                    impacted_resources.append(f'DaemonSet: kube-system/aws-node (service account: {service_account_name or "default"})')
                    service_account_status = 'default'
                else:
                    # Uses any other service account - compliant
                    is_compliant = True
                    service_account_status = 'custom'
                    logger.info(f'VPC CNI uses custom service account: {service_account_name}')
                
                risk_level = 'medium' if not is_compliant else 'low'
                
                # Build details string
                if is_compliant:
                    details = f'VPC CNI uses dedicated IAM role via service account ({service_account_name})'
                else:
                    details = 'VPC CNI using node instance profile instead of dedicated IAM role'
                
                return self._create_check_result('N5', is_compliant, impacted_resources, details)
                
            except Exception as k8s_error:
                logger.error(f'Error querying aws-node daemonset: {str(k8s_error)}')
                return self._create_check_result('N5', False, [], f'Failed to query aws-node daemonset: {str(k8s_error)}')
                
        except Exception as e:
            logger.error(f'Error checking self-managed VPC CNI service account: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N5', False, [], f'Failed to check self-managed VPC CNI service account: {str(e)}')

    async def _check_subnet_ip_availability(self, cluster_name: str, region: Optional[str], n3_result: Optional[Dict[str, Any]] = None, n4_result: Optional[Dict[str, Any]] = None, n5_result: Optional[Dict[str, Any]] = None, k8s_client=None) -> Dict[str, Any]:
        """Check N6: Subnet IP Address Availability."""
        try:
            logger.info(f'Checking subnet IP availability for cluster: {cluster_name}')
            
            # Extract subnet analysis from N3 results
            if not n3_result:
                # If N3 didn't run, we can't perform this check
                return self._create_check_result('N6', True, [], 'Skipped: N3 check did not run, subnet analysis not available')
            
            # Get subnet analysis from N3's internal data
            subnet_analysis = n3_result.get('_subnet_analysis', {})
            
            if not subnet_analysis:
                return self._create_check_result('N6', True, [], 'Skipped: no subnet analysis data available')
            
            # Extract VPC CNI version and configuration from N4/N5 results
            vpc_cni_version = self._extract_vpc_cni_version(n4_result, n5_result)
            enhanced_subnet_discovery_supported = self._supports_enhanced_subnet_discovery(vpc_cni_version)
            
            # Extract VPC CNI environment variables directly from daemonset
            vpc_cni_env_vars = await self._extract_vpc_cni_env_vars(k8s_client) if k8s_client else {}
            
            # Analyze IP availability
            low_ip_subnets = []
            critical_ip_subnets = []
            subnet_details = subnet_analysis.get('subnet_details', {})
            
            for subnet_id, subnet_info in subnet_details.items():
                available_ips = subnet_info.get('available_ip_address_count', 0)
                utilization = subnet_info.get('ip_utilization_percent', 0)
                
                if available_ips < 10:
                    critical_ip_subnets.append({
                        'subnet_id': subnet_id,
                        'available_ips': available_ips,
                        'utilization_percent': utilization,
                        'availability_zone': subnet_info.get('availability_zone', 'unknown')
                    })
                elif available_ips < 20:
                    low_ip_subnets.append({
                        'subnet_id': subnet_id,
                        'available_ips': available_ips,
                        'utilization_percent': utilization,
                        'availability_zone': subnet_info.get('availability_zone', 'unknown')
                    })
            
            # Determine compliance
            impacted_resources = []
            issues = []
            
            if critical_ip_subnets:
                is_compliant = False
                issues.append(f'{len(critical_ip_subnets)} subnet(s) have critically low IP addresses (<10 available)')
                for subnet in critical_ip_subnets:
                    impacted_resources.append(f'Subnet: {subnet["subnet_id"]} ({subnet["available_ips"]} IPs available)')
            elif low_ip_subnets:
                is_compliant = False
                issues.append(f'{len(low_ip_subnets)} subnet(s) have low IP addresses (<20 available)')
                for subnet in low_ip_subnets:
                    impacted_resources.append(f'Subnet: {subnet["subnet_id"]} ({subnet["available_ips"]} IPs available)')
            else:
                is_compliant = True
            
            risk_level = 'critical' if critical_ip_subnets else ('high' if low_ip_subnets else 'low')
            
            # Build details string
            total_low = len(low_ip_subnets) + len(critical_ip_subnets)
            if critical_ip_subnets:
                details = f'{len(critical_ip_subnets)} subnets with critically low IPs'
                if low_ip_subnets:
                    details += f', {len(low_ip_subnets)} with low IPs'
            elif low_ip_subnets:
                details = f'{len(low_ip_subnets)} subnets with low IPs'
            else:
                details = 'All subnets have sufficient IP availability'
            
            return self._create_check_result('N6', is_compliant, impacted_resources, details)
            
        except Exception as e:
            logger.error(f'Error checking subnet IP availability: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N6', False, [], f'Failed to check subnet IP availability: {str(e)}')

    async def _check_load_balancer_target_type(self, cluster_name: str, region: Optional[str], k8s_client) -> Dict[str, Any]:
        """Check N7: Load Balancer Target Type Configuration."""
        try:
            logger.info(f'Checking load balancer target type configuration for cluster: {cluster_name}')
            
            if not k8s_client:
                return self._create_check_result('N7', True, [], 'Skipped: Kubernetes client not available')
            
            # Check Ingress resources for ALB target type
            ingress_issues = []
            ingress_compliant = []
            all_ingress_list = []  # Store for N8 to reuse
            
            try:
                ingress_response = k8s_client.list_resources(
                    kind='Ingress',
                    api_version='networking.k8s.io/v1'
                )
                
                if hasattr(ingress_response, 'items') and ingress_response.items:
                    for ingress in ingress_response.items:
                        ingress_dict = ingress.to_dict() if hasattr(ingress, 'to_dict') else ingress
                        metadata = ingress_dict.get('metadata', {})
                        ingress_name = metadata.get('name', 'unknown')
                        namespace = metadata.get('namespace', 'default')
                        annotations = metadata.get('annotations', {})
                        
                        # Store full ingress info for N8
                        all_ingress_list.append({
                            'name': ingress_name,
                            'namespace': namespace,
                            'spec': ingress_dict.get('spec', {})
                        })
                        
                        # Check for ALB target type annotation
                        target_type = annotations.get('alb.ingress.kubernetes.io/target-type', 'instance')
                        
                        if target_type.lower() != 'ip':
                            ingress_issues.append({
                                'name': ingress_name,
                                'namespace': namespace,
                                'target_type': target_type,
                                'annotation': 'alb.ingress.kubernetes.io/target-type'
                            })
                        else:
                            ingress_compliant.append({
                                'name': ingress_name,
                                'namespace': namespace,
                                'target_type': target_type
                            })
                            
            except Exception as ingress_error:
                logger.warning(f'Error querying Ingress resources: {str(ingress_error)}')
            
            # Check LoadBalancer Services for NLB target type
            service_issues = []
            service_compliant = []
            
            try:
                service_response = k8s_client.list_resources(
                    kind='Service',
                    api_version='v1'
                )
                
                if hasattr(service_response, 'items') and service_response.items:
                    for service in service_response.items:
                        service_dict = service.to_dict() if hasattr(service, 'to_dict') else service
                        metadata = service_dict.get('metadata', {})
                        spec = service_dict.get('spec', {})
                        
                        # Only check LoadBalancer type services
                        if spec.get('type') != 'LoadBalancer':
                            continue
                        
                        service_name = metadata.get('name', 'unknown')
                        namespace = metadata.get('namespace', 'default')
                        annotations = metadata.get('annotations', {})
                        
                        # Check for NLB target type annotation
                        target_type = annotations.get('service.beta.kubernetes.io/aws-load-balancer-nlb-target-type', 'instance')
                        
                        # Also check the newer annotation format
                        if 'service.beta.kubernetes.io/aws-load-balancer-nlb-target-type' not in annotations:
                            target_type = annotations.get('service.beta.kubernetes.io/aws-load-balancer-target-type', 'instance')
                        
                        if target_type.lower() != 'ip':
                            service_issues.append({
                                'name': service_name,
                                'namespace': namespace,
                                'target_type': target_type,
                                'annotation': 'service.beta.kubernetes.io/aws-load-balancer-nlb-target-type'
                            })
                        else:
                            service_compliant.append({
                                'name': service_name,
                                'namespace': namespace,
                                'target_type': target_type
                            })
                            
            except Exception as service_error:
                logger.warning(f'Error querying Service resources: {str(service_error)}')
            
            # Determine compliance
            impacted_resources = []
            issues = []
            
            for ingress in ingress_issues:
                impacted_resources.append(f'Ingress: {ingress["namespace"]}/{ingress["name"]} (target-type: {ingress["target_type"]})')
                
            for service in service_issues:
                impacted_resources.append(f'Service: {service["namespace"]}/{service["name"]} (target-type: {service["target_type"]})')
            
            is_compliant = len(ingress_issues) == 0 and len(service_issues) == 0
            
            if ingress_issues:
                issues.append(f'{len(ingress_issues)} Ingress resource(s) not using IP target mode')
            if service_issues:
                issues.append(f'{len(service_issues)} LoadBalancer Service(s) not using IP target mode')
            
            risk_level = 'medium' if (ingress_issues or service_issues) else 'low'
            
            # Build details string
            total_issues = len(ingress_issues) + len(service_issues)
            if total_issues == 0:
                details = 'All services and ingresses use IP target mode'
            else:
                parts = []
                if ingress_issues:
                    parts.append(f'{len(ingress_issues)} Ingress using instance target mode instead of IP mode')
                if service_issues:
                    parts.append(f'{len(service_issues)} LoadBalancer service using instance target mode instead of IP mode')
                details = ', '.join(parts)
            
            # Store all_ingress_list in the result for N8 to consume
            result = self._create_check_result('N7', is_compliant, impacted_resources, details)
            result['_all_ingress_list'] = all_ingress_list
            return result
            
        except Exception as e:
            logger.error(f'Error checking load balancer target type: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N7', False, [], f'Failed to check load balancer target type: {str(e)}')

    async def _check_prestop_hooks_for_ingress(self, cluster_name: str, region: Optional[str], k8s_client, n7_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Check N8: PreStop Hook Configuration for Ingress Deployments."""
        try:
            logger.info(f'Checking PreStop hooks for Deployments behind Ingress in cluster: {cluster_name}')
            
            if not k8s_client:
                return self._create_check_result('N8', True, [], 'Skipped: Kubernetes client not available')
            
            # Get Ingress list from N7 results (stored in _all_ingress_list)
            all_ingress_list = []
            if n7_result:
                all_ingress_list = n7_result.get('_all_ingress_list', [])
            
            if not all_ingress_list:
                return self._create_check_result('N8', True, [], 'Skipped: no Ingress resources found or N7 data not available')
            
            # For each Ingress, find backend services and check their Deployments for PreStop hooks
            deployments_without_prestop = []
            deployments_with_prestop = []
            deployments_seen = set()  # Deduplicate by namespace/name
            services_checked = set()
            deployments_by_namespace = {}  # Cache: namespace -> list of deployment dicts
            
            for ingress_info in all_ingress_list:
                ingress_name = ingress_info.get('name', 'unknown')
                namespace = ingress_info.get('namespace', 'default')
                spec = ingress_info.get('spec', {})
                
                # Extract backend services from Ingress spec
                backend_services = self._extract_backend_services_from_ingress(spec)
                
                # Fetch all Deployments in this namespace once (Option A)
                if namespace not in deployments_by_namespace:
                    try:
                        dep_response = k8s_client.list_resources(
                            kind='Deployment', api_version='apps/v1', namespace=namespace
                        )
                        ns_deployments = []
                        if hasattr(dep_response, 'items') and dep_response.items:
                            for dep in dep_response.items:
                                dep_dict = dep.to_dict() if hasattr(dep, 'to_dict') else dep
                                ns_deployments.append(dep_dict)
                        deployments_by_namespace[namespace] = ns_deployments
                    except Exception as e:
                        logger.warning(f'Error fetching deployments in namespace {namespace}: {str(e)}')
                        deployments_by_namespace[namespace] = []
                
                for service_name in backend_services:
                    service_key = f'{namespace}/{service_name}'
                    if service_key in services_checked:
                        continue
                    services_checked.add(service_key)
                    
                    try:
                        # Get the Service to find its selector
                        service_response = k8s_client.list_resources(
                            kind='Service',
                            api_version='v1',
                            namespace=namespace,
                            field_selector=f'metadata.name={service_name}'
                        )
                        
                        if not hasattr(service_response, 'items') or not service_response.items:
                            continue
                        
                        service_dict = service_response.items[0].to_dict() if hasattr(service_response.items[0], 'to_dict') else service_response.items[0]
                        svc_selector = service_dict.get('spec', {}).get('selector', {})
                        
                        if not svc_selector:
                            continue
                        
                        # Match Deployments whose spec.selector.matchLabels is a superset of the service selector
                        for dep_dict in deployments_by_namespace.get(namespace, []):
                            dep_match_labels = dep_dict.get('spec', {}).get('selector', {}).get('matchLabels', {})
                            
                            # Check if all service selector labels are present in the deployment's matchLabels
                            if not all(dep_match_labels.get(k) == v for k, v in svc_selector.items()):
                                continue
                            
                            dep_metadata = dep_dict.get('metadata', {})
                            deployment_name = dep_metadata.get('name', 'unknown')
                            
                            # Deduplicate — same deployment can match multiple services/ingress paths
                            dep_key = f'{namespace}/{deployment_name}'
                            if dep_key in deployments_seen:
                                continue
                            deployments_seen.add(dep_key)
                            
                            template_spec = dep_dict.get('spec', {}).get('template', {}).get('spec', {})
                            containers = template_spec.get('containers', [])
                            
                            # Check if any container has PreStop hook
                            has_prestop = False
                            prestop_details = []
                            for container in containers:
                                lifecycle = container.get('lifecycle', {})
                                if lifecycle and lifecycle.get('preStop') is not None:
                                    has_prestop = True
                                    prestop_details.append({
                                        'container': container.get('name', 'unknown'),
                                        'preStop': lifecycle['preStop']
                                    })
                            
                            deployment_info = {
                                'deployment_name': deployment_name,
                                'namespace': namespace,
                                'service': service_name,
                                'ingress': ingress_name
                            }
                            
                            if has_prestop:
                                deployment_info['prestop_hooks'] = prestop_details
                                deployments_with_prestop.append(deployment_info)
                            else:
                                deployments_without_prestop.append(deployment_info)
                                    
                    except Exception as deployment_error:
                        logger.warning(f'Error checking deployments for service {service_name}: {str(deployment_error)}')
            
            # Determine compliance — deduplicate impacted_resources to unique deployments only
            impacted_resources = []
            seen_deployments = set()
            issues = []
            
            for deployment in deployments_without_prestop:
                dep_key = f'{deployment["namespace"]}/{deployment["deployment_name"]}'
                if dep_key not in seen_deployments:
                    seen_deployments.add(dep_key)
                    impacted_resources.append(dep_key)
            
            is_compliant = len(deployments_without_prestop) == 0
            
            if deployments_without_prestop:
                issues.append(f'{len(impacted_resources)} unique Deployment(s) behind Ingress resources missing PreStop hooks')
            
            risk_level = 'medium' if deployments_without_prestop else 'low'
            
            # Build details string
            total_checked = len(deployments_with_prestop) + len(deployments_without_prestop)
            if deployments_without_prestop:
                details = f'{len(impacted_resources)} deployments behind Ingress missing PreStop hooks'
            else:
                details = 'All ingress-backed deployments have PreStop hooks'
            
            return self._create_check_result('N8', is_compliant, impacted_resources, details)
            
        except Exception as e:
            logger.error(f'Error checking PreStop hooks: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N8', False, [], f'Failed to check PreStop hooks: {str(e)}')

    def _extract_backend_services_from_ingress(self, ingress_spec: Dict[str, Any]) -> List[str]:
        """Extract backend service names from Ingress spec."""
        services = set()
        
        try:
            # Check default backend
            default_backend = ingress_spec.get('defaultBackend', {})
            if 'service' in default_backend:
                service_name = default_backend['service'].get('name')
                if service_name:
                    services.add(service_name)
            
            # Check rules
            rules = ingress_spec.get('rules', [])
            for rule in rules:
                http = rule.get('http', {})
                paths = http.get('paths', [])
                for path in paths:
                    backend = path.get('backend', {})
                    if 'service' in backend:
                        service_name = backend['service'].get('name')
                        if service_name:
                            services.add(service_name)
                            
        except Exception as e:
            logger.warning(f'Error extracting backend services from Ingress spec: {str(e)}')
        
        return list(services)

    async def _check_kube_proxy_mode_for_scale(self, cluster_name: str, region: Optional[str], k8s_client) -> Dict[str, Any]:
        """Check N9: Kube-Proxy Mode for Service Scale."""
        try:
            logger.info(f'Checking kube-proxy mode for cluster: {cluster_name}')
            
            if not k8s_client:
                return self._create_check_result('N9', True, [], 'Skipped: Kubernetes client not available')
            
            # Detect kube-proxy mode
            proxy_mode = await self._detect_kube_proxy_mode(k8s_client)
            
            # Count services
            service_count = await self._count_services(k8s_client)
            
            # Determine compliance
            is_compliant = True
            impacted_resources = []
            issues = []
            risk_level = 'low'
            
            if proxy_mode == 'unknown':
                # Cannot determine compliance if mode is unknown
                is_compliant = True
                risk_level = 'low'
                issues.append(
                    f'Could not detect kube-proxy mode. Cluster has {service_count} services. '
                    f'Manual verification recommended if service count > 1000.'
                )
            elif proxy_mode == 'iptables' and service_count > 1000:
                is_compliant = False
                risk_level = 'high'
                issues.append(
                    f'Cluster has {service_count} services but uses iptables mode. '
                    f'IPVS mode recommended for >1000 services.'
                )
                impacted_resources.append(
                    f'kube-proxy (mode: {proxy_mode}, services: {service_count})'
                )
            elif proxy_mode == 'iptables' and service_count > 500:
                # Warning zone
                risk_level = 'medium'
                issues.append(
                    f'Cluster has {service_count} services with iptables mode. '
                    f'Consider migrating to IPVS before reaching 1000 services.'
                )
            
            # Build details string
            if proxy_mode == 'unknown':
                details = f'Could not detect kube-proxy mode, cluster has {service_count} services'
            elif proxy_mode == 'iptables' and service_count > 1000:
                details = f'kube-proxy using iptables mode with {service_count} services, consider IPVS'
            elif proxy_mode == 'iptables' and service_count > 500:
                details = f'kube-proxy using iptables mode with {service_count} services, approaching IPVS threshold'
            else:
                details = f'kube-proxy using {proxy_mode} mode with {service_count} services (appropriate)'
            
            return self._create_check_result('N9', is_compliant, impacted_resources, details)
            
        except Exception as e:
            logger.error(f'Error checking kube-proxy mode: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return self._create_check_result('N9', False, [], f'Failed to check kube-proxy mode: {str(e)}')

    async def _detect_kube_proxy_mode(self, k8s_client) -> str:
        """Detect kube-proxy mode. Returns: 'iptables', 'ipvs', or 'unknown'."""
        try:
            # Method 1: Check kube-proxy ConfigMap
            try:
                configmap_response = k8s_client.list_resources(
                    kind='ConfigMap',
                    api_version='v1',
                    namespace='kube-system',
                    field_selector='metadata.name=kube-proxy-config'
                )
                
                if hasattr(configmap_response, 'items') and configmap_response.items:
                    cm_dict = configmap_response.items[0].to_dict() if hasattr(configmap_response.items[0], 'to_dict') else configmap_response.items[0]
                    data = cm_dict.get('data', {})
                    config = data.get('config.conf', '') or data.get('config', '')
                    
                    # Parse config for mode
                    if 'mode: "ipvs"' in config or 'mode: ipvs' in config or 'mode:"ipvs"' in config:
                        logger.info('Detected kube-proxy mode: ipvs (from ConfigMap)')
                        return 'ipvs'
                    elif 'mode: "iptables"' in config or 'mode: iptables' in config or 'mode:"iptables"' in config:
                        logger.info('Detected kube-proxy mode: iptables (from ConfigMap)')
                        return 'iptables'
            except Exception as cm_error:
                logger.warning(f'Error checking kube-proxy ConfigMap: {str(cm_error)}')
            
            # Method 2: Check kube-proxy DaemonSet
            try:
                ds_response = k8s_client.list_resources(
                    kind='DaemonSet',
                    api_version='apps/v1',
                    namespace='kube-system',
                    field_selector='metadata.name=kube-proxy'
                )
                
                if hasattr(ds_response, 'items') and ds_response.items:
                    ds_dict = ds_response.items[0].to_dict() if hasattr(ds_response.items[0], 'to_dict') else ds_response.items[0]
                    spec = ds_dict.get('spec', {})
                    template = spec.get('template', {})
                    pod_spec = template.get('spec', {})
                    containers = pod_spec.get('containers', [])
                    
                    for container in containers:
                        if container.get('name') == 'kube-proxy':
                            command = container.get('command', [])
                            args = container.get('args', [])
                            
                            # Check command and args for --proxy-mode flag
                            all_params = command + args
                            for param in all_params:
                                if '--proxy-mode=ipvs' in str(param):
                                    logger.info('Detected kube-proxy mode: ipvs (from DaemonSet)')
                                    return 'ipvs'
                                elif '--proxy-mode=iptables' in str(param):
                                    logger.info('Detected kube-proxy mode: iptables (from DaemonSet)')
                                    return 'iptables'
            except Exception as ds_error:
                logger.warning(f'Error checking kube-proxy DaemonSet: {str(ds_error)}')
            
            # Could not detect mode
            logger.warning('Could not detect kube-proxy mode from ConfigMap or DaemonSet')
            return 'unknown'
            
        except Exception as e:
            logger.warning(f'Error detecting kube-proxy mode: {str(e)}')
            return 'unknown'

    async def _count_services(self, k8s_client) -> int:
        """Count total number of Services in the cluster."""
        try:
            service_response = k8s_client.list_resources(
                kind='Service',
                api_version='v1'
            )
            
            if hasattr(service_response, 'items'):
                count = len(service_response.items)
                logger.info(f'Total services in cluster: {count}')
                return count
            
            return 0
        except Exception as e:
            logger.error(f'Error counting services: {str(e)}')
            return 0

    def _extract_vpc_cni_version(self, n4_result: Optional[Dict[str, Any]], n5_result: Optional[Dict[str, Any]]) -> str:
        """Extract VPC CNI version from N4 or N5 results."""
        try:
            # Try N5 first (managed add-on has version info)
            if n5_result and n5_result.get('compliant'):
                service_account_analysis = n5_result.get('_service_account_analysis', {})
                if service_account_analysis.get('management_type') == 'eks_managed':
                    version = service_account_analysis.get('addon_version', '')
                    if version:
                        return version
            
            # Try N4 as fallback
            if n4_result:
                vpc_cni_analysis = n4_result.get('_vpc_cni_analysis', {})
                image = vpc_cni_analysis.get('image', '')
                if image and ':v' in image:
                    # Extract version from image URL
                    version = image.split(':v')[-1].split('-')[0]
                    return f'v{version}'
            
            return 'unknown'
            
        except Exception as e:
            logger.warning(f'Error extracting VPC CNI version: {str(e)}')
            return 'unknown'

    def _supports_enhanced_subnet_discovery(self, vpc_cni_version: str) -> bool:
        """Check if VPC CNI version supports enhanced subnet discovery (available since v1.18.0)."""
        try:
            if vpc_cni_version == 'unknown':
                return False
            
            # Parse version (e.g., v1.19.5 -> [1, 19, 5])
            version_str = vpc_cni_version.lstrip('v')
            version_parts = [int(x) for x in version_str.split('.') if x.isdigit()]
            
            if len(version_parts) >= 2:
                major, minor = version_parts[0], version_parts[1]
                # Enhanced subnet discovery available since v1.18.0
                return (major > 1) or (major == 1 and minor >= 18)
            
            return False
            
        except Exception as e:
            logger.warning(f'Error checking enhanced subnet discovery support: {str(e)}')
            return False

    async def _extract_vpc_cni_env_vars(self, k8s_client) -> Dict[str, Any]:
        """Extract VPC CNI environment variables from aws-node daemonset."""
        try:
            if not k8s_client:
                return {'error': 'Kubernetes client not available'}
            
            # Query aws-node daemonset
            daemonset_response = k8s_client.list_resources(
                kind='DaemonSet',
                api_version='apps/v1',
                namespace='kube-system',
                field_selector='metadata.name=aws-node'
            )
            
            if not hasattr(daemonset_response, 'items') or not daemonset_response.items:
                return {'error': 'aws-node daemonset not found'}
            
            # Get the aws-node daemonset
            aws_node_ds = daemonset_response.items[0]
            ds_dict = aws_node_ds.to_dict() if hasattr(aws_node_ds, 'to_dict') else aws_node_ds
            
            # Navigate to container spec
            spec = ds_dict.get('spec', {})
            template = spec.get('template', {})
            template_spec = template.get('spec', {})
            containers = template_spec.get('containers', [])
            
            # Find aws-node container
            aws_node_container = None
            for container in containers:
                if container.get('name') == 'aws-node':
                    aws_node_container = container
                    break
            
            if not aws_node_container:
                return {'error': 'aws-node container not found in daemonset'}
            
            # Extract environment variables
            env_vars = aws_node_container.get('env', [])
            
            # Target specific IP allocation related variables
            target_vars = {
                'WARM_IP_TARGET': None,
                'MINIMUM_IP_TARGET': None,
                'WARM_ENI_TARGET': None,
                'ENABLE_PREFIX_DELEGATION': None,
                'AWS_VPC_K8S_CNI_CUSTOM_NETWORK_CFG': None,
                'POD_SECURITY_GROUP_ENFORCING_MODE': None
            }
            
            for env_var in env_vars:
                var_name = env_var.get('name', '')
                if var_name in target_vars:
                    # Get value from direct value or valueFrom
                    value = env_var.get('value')
                    if value is None and 'valueFrom' in env_var:
                        # Handle valueFrom (configMap, secret, etc.)
                        value_from = env_var.get('valueFrom', {})
                        if 'configMapKeyRef' in value_from:
                            value = f"<from ConfigMap: {value_from['configMapKeyRef'].get('name', 'unknown')}>"
                        elif 'secretKeyRef' in value_from:
                            value = f"<from Secret: {value_from['secretKeyRef'].get('name', 'unknown')}>"
                        else:
                            value = '<from valueFrom>'
                    target_vars[var_name] = value
            
            logger.info(f'Extracted VPC CNI environment variables: {target_vars}')
            return target_vars
            
        except Exception as e:
            logger.warning(f'Error extracting VPC CNI environment variables: {str(e)}')
            return {'error': f'Failed to extract environment variables: {str(e)}'}

    def _determine_iam_method(self, has_service_account_role: bool, has_pod_identity_associations: bool) -> str:
        """Determine the IAM method being used for VPC CNI."""
        if has_service_account_role and has_pod_identity_associations:
            return 'both_service_account_and_pod_identity'
        elif has_service_account_role:
            return 'service_account_role'
        elif has_pod_identity_associations:
            return 'pod_identity_associations'
        else:
            return 'none'

    def _calculate_total_ips(self, cidr_block: str) -> int:
        """Calculate total IP addresses in a CIDR block."""
        try:
            if not cidr_block or '/' not in cidr_block:
                return 0
            
            # Extract prefix length (e.g., /24 from 10.0.1.0/24)
            prefix_length = int(cidr_block.split('/')[-1])
            
            # Calculate total IPs: 2^(32-prefix_length) - 5 (AWS reserves 5 IPs per subnet)
            total_ips = (2 ** (32 - prefix_length)) - 5
            return max(0, total_ips)
            
        except Exception as e:
            logger.warning(f'Error calculating total IPs for CIDR {cidr_block}: {str(e)}')
            return 0

    def _calculate_ip_utilization(self, available_ips: int, cidr_block: str) -> float:
        """Calculate IP utilization percentage."""
        try:
            total_ips = self._calculate_total_ips(cidr_block)
            if total_ips == 0:
                return 0.0
            
            used_ips = total_ips - available_ips
            utilization = (used_ips / total_ips) * 100
            return round(utilization, 2)
            
        except Exception as e:
            logger.warning(f'Error calculating IP utilization: {str(e)}')
            return 0.0


