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

"""Handler for Karpenter best practices checks in the EKS MCP Server."""

import json
from pathlib import Path
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.check_utils import compact_response
from awslabs.eks_review_mcp_server.logging_helper import LogLevel, log_with_request_id
from awslabs.eks_review_mcp_server.models import KarpenterCheckResponse
from collections import Counter
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, Optional, List


class EKSKarpenterHandler:
    """Handler for Karpenter best practices checks in the EKS MCP Server."""

    def __init__(self, mcp, client_cache):
        """Initialize the EKS Karpenter handler.

        Args:
            mcp: The MCP server instance
            client_cache: K8sClientCache instance to share between handlers
        """
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the comprehensive check tool
        self.mcp.tool(name='check_karpenter_best_practices')(self.check_karpenter_best_practices)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_karpenter_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load check registry: {e}")
            return {}

    def _get_all_checks(self, category: str = 'karpenter') -> Dict[str, Dict[str, Any]]:
        """Get all checks flattened into a single dictionary."""
        all_checks = {}
        all_checks.update(self.check_registry.get(category, {}))
        return all_checks

    def _get_check_info(self, check_id: str) -> Dict[str, Any]:
        """Get check information by ID."""
        # Check in both karpenter and auto_mode categories
        for category in ['karpenter', 'auto_mode']:
            checks = self._get_all_checks(category)
            if check_id in checks:
                return checks[check_id]
        return {}

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
            'severity': check_info.get('severity', 'High'),
            'compliant': compliant,
            'impacted_resources': impacted_resources,
            'details': details,
        }

    def _create_check_error_result(self, check_id: str, error_msg: str) -> Dict[str, Any]:
        """Create an error result for a failed check."""
        check_info = self._get_check_info(check_id)
        return {
            'check_name': check_info.get('name', f'Check {check_id}'),
            'severity': check_info.get('severity', 'High'),
            'compliant': False,
            'impacted_resources': [],
            'details': f'Check failed with error: {error_msg}',
            'remediation': '',
        }

    def _create_error_response(self, cluster_name: str, error_msg: str) -> KarpenterCheckResponse:
        """Create an error response."""
        return KarpenterCheckResponse(
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

    async def check_karpenter_best_practices(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for Karpenter best practices.'
        ),
        namespace: Optional[str] = Field(
            None, description='Optional namespace to limit the check scope.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
    ) -> KarpenterCheckResponse:
        """Check EKS cluster for Karpenter best practices.

        This tool runs a comprehensive set of Karpenter best practices checks against your EKS cluster
        to identify potential issues and provides remediation guidance.

        The tool evaluates Karpenter configuration and deployment best practices.
        """
        try:
            logger.info(f'Starting Karpenter best practices check for cluster: {cluster_name}')

            # Get K8s client for the cluster
            try:
                k8s_client = self.client_cache.get_client(cluster_name, region=region)
                logger.info(f'Successfully obtained K8s client for cluster: {cluster_name}')
            except Exception as e:
                logger.error(f'Failed to get K8s client for cluster {cluster_name}: {str(e)}')
                return self._create_error_response(cluster_name, str(e))

            # Initialize clients and fetch shared data once (optimization)
            shared_data = await self._initialize_clients_and_data(k8s_client, cluster_name, namespace, region)
            if not shared_data:
                return self._create_error_response(cluster_name, "Failed to initialize clients and fetch data")
            
            # Early exit for Auto Mode (optimization)
            if shared_data.get('skip_karpenter_checks'):
                logger.info('Auto Mode detected - skipping Karpenter checks')
                auto_mode_features = shared_data.get('auto_mode_features', {})
                enabled_features = [k for k, v in auto_mode_features.items() if v]
                
                check_results = [self._create_check_result(
                    'A1',
                    True,
                    [],
                    f'EKS Auto Mode is enabled ({", ".join(enabled_features)}) - Karpenter checks not applicable'
                )]
                
                summary = f'Cluster {cluster_name} uses EKS Auto Mode - Karpenter checks not applicable'
                
                return KarpenterCheckResponse(
                    isError=False,
                    content=[TextContent(type='text', text=summary)],
                    check_results=check_results,
                    overall_compliant=True,
                    summary=summary,
                )
            
            # Check if Karpenter is deployed
            karpenter_found = len(shared_data.get('karpenter_deployments', [])) > 0
            karpenter_result = self._create_karpenter_deployment_result(shared_data, cluster_name)
            
            check_results = []
            all_compliant = True
            
            # If Karpenter is deployed, run all Karpenter checks
            if karpenter_found:
                logger.info('Self-managed Karpenter found - running Karpenter best practices checks')
                
                # Add the Karpenter deployment check result
                check_results.append(karpenter_result)
                
                # Get remaining Karpenter checks (K2-K9) and sort by ID
                karpenter_checks = self._get_all_checks('karpenter')
                remaining_checks = {k: v for k, v in karpenter_checks.items() if k != 'K1'}
                
                for check_id in sorted(remaining_checks.keys()):
                    try:
                        logger.info(f'Running check {check_id}')
                        result = await self._execute_check(check_id, shared_data, cluster_name)
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
                logger.info('Neither Karpenter nor Auto Mode found')
                check_results.append(karpenter_result)
                all_compliant = False

            # Generate summary
            passed_count = sum(1 for r in check_results if r['compliant'])
            failed_count = len(check_results) - passed_count
            summary = f'Cluster {cluster_name} Karpenter best practices check: {passed_count} checks passed, {failed_count} checks failed'

            # Create detailed response with JSON data in content
            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return KarpenterCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in Karpenter best practices check: {str(e)}')
            return self._create_error_response(cluster_name, str(e))

    async def _initialize_clients_and_data(self, k8s_client, cluster_name: str, namespace: Optional[str], region: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Initialize clients and fetch shared data once (optimization)."""
        try:
            shared_data = {}
            
            # Initialize EKS client once
            eks_client = AwsHelper.create_boto3_client('eks', region_name=region)
            shared_data['eks_client'] = eks_client
            
            # Check Auto Mode FIRST (early exit optimization)
            try:
                cluster_response = eks_client.describe_cluster(name=cluster_name)
                cluster_info = cluster_response['cluster']
                shared_data['cluster_info'] = cluster_info
                
                compute_config = cluster_info.get('computeConfig', {})
                storage_config = cluster_info.get('storageConfig', {})
                kubernetes_network_config = cluster_info.get('kubernetesNetworkConfig', {})
                elastic_load_balancing = kubernetes_network_config.get('elasticLoadBalancing', {})
                
                is_auto_mode = (
                    compute_config.get('enabled', False) or
                    storage_config.get('blockStorage', {}).get('enabled', False) or
                    elastic_load_balancing.get('enabled', False)
                )
                
                if is_auto_mode:
                    logger.info('EKS Auto Mode detected - Karpenter checks not applicable')
                    shared_data['is_auto_mode'] = True
                    shared_data['skip_karpenter_checks'] = True
                    shared_data['auto_mode_features'] = {
                        'compute_enabled': compute_config.get('enabled', False),
                        'storage_enabled': storage_config.get('blockStorage', {}).get('enabled', False),
                        'elastic_load_balancing_enabled': elastic_load_balancing.get('enabled', False)
                    }
                    return shared_data  # Early exit!
                
                shared_data['is_auto_mode'] = False
                
            except Exception as e:
                logger.warning(f'Failed to check Auto Mode: {str(e)}')
                shared_data['is_auto_mode'] = False
            
            # Fetch Karpenter deployments ONCE
            try:
                deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1')
                karpenter_deployments = []
                
                for deployment in deployments.items:
                    if 'karpenter' in deployment.metadata.name.lower():
                        karpenter_deployments.append({
                            'name': deployment.metadata.name,
                            'namespace': deployment.metadata.namespace,
                            'deployment': deployment
                        })
                
                shared_data['karpenter_deployments'] = karpenter_deployments
                logger.info(f'Found {len(karpenter_deployments)} Karpenter deployments')
                
                # Pre-parse Karpenter configuration (optimization)
                karpenter_config = self._parse_karpenter_config(karpenter_deployments)
                shared_data['karpenter_config'] = karpenter_config
                
            except Exception as e:
                logger.warning(f'Failed to fetch Karpenter deployments: {str(e)}')
                shared_data['karpenter_deployments'] = []
                shared_data['karpenter_config'] = {}
            
            # Fetch NodePools ONCE (optimization - saves 8 API calls!)
            try:
                nodepools = k8s_client.list_resources(kind='NodePool', api_version='karpenter.sh/v1')
                shared_data['nodepools'] = nodepools.items
                shared_data['nodepool_count'] = len(nodepools.items)
                logger.info(f'Found {len(nodepools.items)} Karpenter NodePools (v1)')
            except Exception as e:
                # Fallback to v1beta1 for older Karpenter versions
                logger.info(f'NodePool v1 not found, trying v1beta1: {str(e)}')
                try:
                    nodepools = k8s_client.list_resources(kind='NodePool', api_version='karpenter.sh/v1beta1')
                    shared_data['nodepools'] = nodepools.items
                    shared_data['nodepool_count'] = len(nodepools.items)
                    logger.info(f'Found {len(nodepools.items)} Karpenter NodePools (v1beta1)')
                except Exception as e2:
                    logger.warning(f'Failed to fetch NodePools (v1 and v1beta1): {str(e2)}')
                    shared_data['nodepools'] = []
                    shared_data['nodepool_count'] = 0
            
            # Fetch EC2NodeClasses ONCE (used by K2 AMI lockdown check)
            try:
                ec2ncs = k8s_client.list_resources(kind='EC2NodeClass', api_version='karpenter.k8s.aws/v1')
                shared_data['ec2_node_classes'] = ec2ncs.items if hasattr(ec2ncs, 'items') else []
                logger.info(f'Found {len(shared_data["ec2_node_classes"])} EC2NodeClasses (v1)')
            except Exception:
                try:
                    ec2ncs = k8s_client.list_resources(kind='EC2NodeClass', api_version='karpenter.k8s.aws/v1beta1')
                    shared_data['ec2_node_classes'] = ec2ncs.items if hasattr(ec2ncs, 'items') else []
                    logger.info(f'Found {len(shared_data["ec2_node_classes"])} EC2NodeClasses (v1beta1)')
                except Exception:
                    shared_data['ec2_node_classes'] = []
            
            return shared_data
            
        except Exception as e:
            logger.error(f'Failed to initialize clients and data: {str(e)}')
            import traceback
            logger.error(f'Traceback: {traceback.format_exc()}')
            return None

    def _parse_karpenter_config(self, karpenter_deployments: List) -> Dict[str, Any]:
        """Parse Karpenter deployment configuration once (optimization)."""
        config = {
            'feature_gates': {},
            'env_vars': {},
            'resource_limits': {},
            'resource_requests': {},
            'version': None
        }
        
        try:
            for karp_dep in karpenter_deployments:
                deployment = karp_dep['deployment']
                containers = deployment.spec.template.spec.get('containers', [])
                
                for container in containers:
                    if 'karpenter' in container.get('name', '').lower():
                        # Extract version from image
                        image = container.get('image', '')
                        if ':v' in image:
                            config['version'] = image.split(':v')[1].split('-')[0]  # Handle v0.32.0-abc format
                        
                        # Parse environment variables and feature gates
                        env_vars = container.get('env', [])
                        for env_var in env_vars:
                            name = env_var.get('name')
                            value = env_var.get('value', '')
                            
                            if name == 'FEATURE_GATES':
                                # Parse feature gates (e.g., "SpotToSpotConsolidation=true,Drift=false")
                                for gate in value.split(','):
                                    if '=' in gate:
                                        k, v = gate.split('=', 1)
                                        config['feature_gates'][k.strip()] = v.strip().lower() == 'true'
                            
                            config['env_vars'][name] = value
                        
                        # Extract resource limits and requests
                        resources = container.get('resources', {})
                        config['resource_limits'] = resources.get('limits', {})
                        config['resource_requests'] = resources.get('requests', {})
                        
                        break  # Found Karpenter container
        
        except Exception as e:
            logger.warning(f'Failed to parse Karpenter config: {str(e)}')
        
        return config

    def _create_karpenter_deployment_result(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Create K1 check result from shared data."""
        karpenter_deployments = shared_data.get('karpenter_deployments', [])
        
        if karpenter_deployments:
            deployment_names = [f"{d['namespace']}/{d['name']}" for d in karpenter_deployments]
            return self._create_check_result(
                'K1',
                True,
                deployment_names,
                f'Karpenter deployment found: {deployment_names[0]}'
            )
        else:
            return self._create_check_result(
                'K1',
                False,
                [],
                'Karpenter deployment not found - skipping remaining checks'
            )

    async def _execute_check(self, check_id: str, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Execute a single check based on its ID using shared data."""
        
        # Map check IDs to their corresponding methods
        check_methods = {
            'K2': self._check_ami_lockdown,
            'K3': self._check_instance_type_exclusions,
            'K4': self._check_nodepool_exclusivity,
            'K5': self._check_ttl_configuration,
            'K6': self._check_instance_type_diversity,
            'K7': self._check_nodepool_limits,
            'K8': self._check_disruption_settings,
            'K9': self._check_spot_consolidation,
        }
        
        method = check_methods.get(check_id)
        if method:
            return await method(shared_data, cluster_name)
        else:
            return self._create_check_error_result(check_id, f'Check method not implemented for {check_id}')

    async def _check_ami_lockdown(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if AMIs are locked down in NodePools.
        
        In Karpenter v1, the AMI selection is in EC2NodeClass.amiSelectorTerms,
        not in NodePool.amiFamily. Check for @latest in amiSelectorTerms aliases.
        """
        try:
            ec2_node_classes = shared_data.get('ec2_node_classes', [])
            
            if not ec2_node_classes:
                # Fallback: check NodePool amiFamily if no EC2NodeClasses found
                nodepools = shared_data.get('nodepools', [])
                unlocked = []
                locked = []
                for np in nodepools:
                    name = np.metadata.name
                    ami_family = np.spec.get('template', {}).get('spec', {}).get('amiFamily', '')
                    if ami_family and '@latest' in str(ami_family):
                        unlocked.append(name)
                    else:
                        locked.append(name)
                if unlocked:
                    return self._create_check_result('K2', False, unlocked,
                        f'Found {len(unlocked)} NodePools using dynamic AMI selectors')
                return self._create_check_result('K2', True, locked,
                    f'All {len(locked)} NodePools use locked AMI selectors')
            
            # Check EC2NodeClasses for @latest in amiSelectorTerms
            unlocked_ncs = []
            locked_ncs = []
            
            for nc in ec2_node_classes:
                nc_dict = nc.to_dict() if hasattr(nc, 'to_dict') else nc
                nc_name = nc_dict.get('metadata', {}).get('name', 'unknown')
                spec = nc_dict.get('spec', {})
                
                ami_selector_terms = spec.get('amiSelectorTerms', [])
                uses_latest = False
                for term in ami_selector_terms:
                    alias = term.get('alias', '') if isinstance(term, dict) else ''
                    if '@latest' in str(alias):
                        uses_latest = True
                        break
                
                if uses_latest:
                    unlocked_ncs.append(nc_name)
                else:
                    locked_ncs.append(nc_name)
            
            if unlocked_ncs:
                return self._create_check_result('K2', False, unlocked_ncs,
                    f'Found {len(unlocked_ncs)} EC2NodeClasses using @latest AMI alias (not pinned)')
            return self._create_check_result('K2', True, locked_ncs,
                f'All {len(locked_ncs)} EC2NodeClasses use pinned AMI selectors')
        except Exception as e:
            return self._create_check_error_result('K2', str(e))

    async def _check_instance_type_exclusions(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if instance types are properly excluded."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            nodepools_without_exclusions = []
            nodepools_with_exclusions = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                requirements = nodepool.spec.get('template', {}).get('spec', {}).get('requirements', [])
                
                has_exclusions = False
                for req in requirements:
                    if req.get('key') == 'node.kubernetes.io/instance-type' and req.get('operator') == 'NotIn':
                        has_exclusions = True
                        break
                
                if has_exclusions:
                    nodepools_with_exclusions.append(nodepool_name)
                else:
                    nodepools_without_exclusions.append(nodepool_name)
            
            if nodepools_without_exclusions:
                return self._create_check_result(
                    'K3',
                    False,
                    nodepools_without_exclusions,
                    f'Found {len(nodepools_without_exclusions)} NodePools without instance type exclusions'
                )
            else:
                return self._create_check_result(
                    'K3',
                    True,
                    nodepools_with_exclusions,
                    f'All {len(nodepools_with_exclusions)} NodePools have instance type exclusions'
                )
        except Exception as e:
            return self._create_check_error_result('K3', str(e))



    async def _check_nodepool_exclusivity(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if NodePools are mutually exclusive or weighted."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            weighted_nodepools = []
            exclusive_nodepools = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                weight = nodepool.spec.get('weight')
                taints = nodepool.spec.get('template', {}).get('spec', {}).get('taints', [])
                
                if weight is not None:
                    weighted_nodepools.append(nodepool_name)
                elif taints:
                    exclusive_nodepools.append(nodepool_name)
            
            total_configured = len(weighted_nodepools) + len(exclusive_nodepools)
            total_nodepools = len(nodepools)
            
            if total_configured == total_nodepools:
                return self._create_check_result(
                    'K4',
                    True,
                    weighted_nodepools + exclusive_nodepools,
                    f'All {total_nodepools} NodePools are properly configured with weights or exclusivity'
                )
            else:
                unconfigured = total_nodepools - total_configured
                return self._create_check_result(
                    'K4',
                    False,
                    [],
                    f'Found {unconfigured} NodePools without proper weight or exclusivity configuration'
                )
        except Exception as e:
            return self._create_check_error_result('K4', str(e))

    async def _check_ttl_configuration(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if TTL is configured for NodePools."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            nodepools_without_ttl = []
            nodepools_with_ttl = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                expire_after = nodepool.spec.get('template', {}).get('spec', {}).get('expireAfter')
                
                if expire_after:
                    nodepools_with_ttl.append(nodepool_name)
                else:
                    nodepools_without_ttl.append(nodepool_name)
            
            if nodepools_without_ttl:
                return self._create_check_result(
                    'K5',
                    False,
                    nodepools_without_ttl,
                    f'Found {len(nodepools_without_ttl)} NodePools without TTL configuration'
                )
            else:
                return self._create_check_result(
                    'K5',
                    True,
                    nodepools_with_ttl,
                    f'All {len(nodepools_with_ttl)} NodePools have TTL configured'
                )
        except Exception as e:
            return self._create_check_error_result('K5', str(e))

    async def _check_instance_type_diversity(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if NodePools allow sufficient instance type diversity."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            constrained_nodepools = []
            diverse_nodepools = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                requirements = nodepool.spec.get('template', {}).get('spec', {}).get('requirements', [])
                
                # Check for overly restrictive instance type constraints
                overly_constrained = False
                for req in requirements:
                    if req.get('key') == 'node.kubernetes.io/instance-type':
                        if req.get('operator') == 'In' and len(req.get('values', [])) < 5:
                            overly_constrained = True
                            break
                
                if overly_constrained:
                    constrained_nodepools.append(nodepool_name)
                else:
                    diverse_nodepools.append(nodepool_name)
            
            if constrained_nodepools:
                return self._create_check_result(
                    'K6',
                    False,
                    constrained_nodepools,
                    f'Found {len(constrained_nodepools)} NodePools with overly constrained instance types'
                )
            else:
                return self._create_check_result(
                    'K6',
                    True,
                    diverse_nodepools,
                    f'All {len(diverse_nodepools)} NodePools allow sufficient instance type diversity'
                )
        except Exception as e:
            return self._create_check_error_result('K6', str(e))

    async def _check_nodepool_limits(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if NodePools have proper limits configured."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            nodepools_without_limits = []
            nodepools_with_limits = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                limits = nodepool.spec.get('limits')
                
                if limits and any(limits.values()):
                    nodepools_with_limits.append(nodepool_name)
                else:
                    nodepools_without_limits.append(nodepool_name)
            
            if nodepools_without_limits:
                return self._create_check_result(
                    'K7',
                    False,
                    nodepools_without_limits,
                    f'Found {len(nodepools_without_limits)} NodePools without resource limits'
                )
            else:
                return self._create_check_result(
                    'K7',
                    True,
                    nodepools_with_limits,
                    f'All {len(nodepools_with_limits)} NodePools have resource limits configured'
                )
        except Exception as e:
            return self._create_check_error_result('K7', str(e))

    async def _check_disruption_settings(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if disruption settings are properly configured."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            nodepools_without_disruption = []
            nodepools_with_disruption = []
            
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                disruption = nodepool.spec.get('disruption', {})
                
                has_consolidation = disruption.get('consolidationPolicy') is not None
                has_consolidate_after = disruption.get('consolidateAfter') is not None
                has_budgets = disruption.get('budgets') is not None
                
                if has_consolidation or has_consolidate_after or has_budgets:
                    nodepools_with_disruption.append(nodepool_name)
                else:
                    nodepools_without_disruption.append(nodepool_name)
            
            if nodepools_without_disruption:
                return self._create_check_result(
                    'K8',
                    False,
                    nodepools_without_disruption,
                    f'Found {len(nodepools_without_disruption)} NodePools without disruption settings'
                )
            else:
                return self._create_check_result(
                    'K8',
                    True,
                    nodepools_with_disruption,
                    f'All {len(nodepools_with_disruption)} NodePools have disruption settings configured'
                )
        except Exception as e:
            return self._create_check_error_result('K8', str(e))

    async def _check_auto_mode(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if EKS Auto Mode is enabled (uses shared data - optimization)."""
        try:
            logger.info(f'A1 Check: Starting Auto Mode detection for cluster: {cluster_name}')
            
            # Use pre-fetched cluster info (optimization)
            cluster_info = shared_data.get('cluster_info', {})
            
            compute_config = cluster_info.get('computeConfig', {})
            storage_config = cluster_info.get('storageConfig', {})
            kubernetes_network_config = cluster_info.get('kubernetesNetworkConfig', {})
            elastic_load_balancing = kubernetes_network_config.get('elasticLoadBalancing', {})
            
            auto_mode_enabled = (
                compute_config.get('enabled', False) or
                storage_config.get('blockStorage', {}).get('enabled', False) or
                elastic_load_balancing.get('enabled', False)
            )
            
            logger.info(f'A1 Check: Auto Mode enabled: {auto_mode_enabled}')
            
            if auto_mode_enabled:
                enabled_features = []
                if compute_config.get('enabled', False):
                    enabled_features.append('compute')
                if storage_config.get('blockStorage', {}).get('enabled', False):
                    enabled_features.append('storage')
                if elastic_load_balancing.get('enabled', False):
                    enabled_features.append('elastic-load-balancing')
                
                return self._create_check_result(
                    'A1',
                    True,
                    [cluster_name],
                    f'EKS Auto Mode is enabled ({", ".join(enabled_features)}) - Karpenter checks not applicable'
                )
            else:
                return self._create_check_result(
                    'A1',
                    False,
                    [],
                    'EKS Auto Mode is not enabled'
                )
            
        except Exception as e:
            logger.error(f'A1 Check: Error during Auto Mode detection: {str(e)}')
            # If there's an error, assume Auto Mode is not enabled
            return self._create_check_result(
                'A1',
                False,
                [],
                f'Auto Mode check failed: {str(e)}'
            )

    async def _check_spot_consolidation(self, shared_data: Dict[str, Any], cluster_name: str) -> Dict[str, Any]:
        """Check if Spot capacity is used and spot-to-spot consolidation is enabled."""
        try:
            # Use pre-fetched NodePools (optimization)
            nodepools = shared_data.get('nodepools', [])
            
            spot_nodepools = []
            for nodepool in nodepools:
                nodepool_name = nodepool.metadata.name
                requirements = nodepool.spec.get('template', {}).get('spec', {}).get('requirements', [])
                
                # Check if NodePool allows Spot instances
                uses_spot = False
                for req in requirements:
                    if req.get('key') == 'karpenter.sh/capacity-type':
                        values = req.get('values', [])
                        # Convert ResourceField list to plain list for safe 'in' check
                        values_list = list(values) if values else []
                        if 'spot' in values_list:
                            uses_spot = True
                            break
                
                if uses_spot:
                    spot_nodepools.append(nodepool_name)
            
            if not spot_nodepools:
                return self._create_check_result(
                    'K9',
                    True,
                    [],
                    'No Spot capacity configured in NodePools - check not applicable'
                )
            
            # Use pre-parsed Karpenter config (optimization)
            karpenter_config = shared_data.get('karpenter_config', {})
            feature_gates = karpenter_config.get('feature_gates', {})
            spot_consolidation_enabled = feature_gates.get('SpotToSpotConsolidation', False)
            
            karpenter_deployments = shared_data.get('karpenter_deployments', [])
            karpenter_deployment = None
            if karpenter_deployments:
                karp_dep = karpenter_deployments[0]
                karpenter_deployment = f"{karp_dep['namespace']}/{karp_dep['name']}"
            
            if spot_consolidation_enabled:
                return self._create_check_result(
                    'K9',
                    True,
                    spot_nodepools + [karpenter_deployment],
                    f'Spot capacity used in {len(spot_nodepools)} NodePools and SpotToSpotConsolidation is enabled'
                )
            else:
                return self._create_check_result(
                    'K9',
                    False,
                    spot_nodepools,
                    f'Spot capacity used in {len(spot_nodepools)} NodePools but SpotToSpotConsolidation is not enabled'
                )
        except Exception as e:
            return self._create_check_error_result('K9', str(e))

