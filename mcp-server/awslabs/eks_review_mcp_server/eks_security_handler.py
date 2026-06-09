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

"""Handler for EKS security checks in the EKS MCP Server."""

import json
from pathlib import Path
from awslabs.eks_review_mcp_server.aws_helper import AwsHelper
from awslabs.eks_review_mcp_server.logging_helper import LogLevel, log_with_request_id
from awslabs.eks_review_mcp_server.models import SecurityCheckResponse
from awslabs.eks_review_mcp_server.check_utils import _aggregate_by_owner, compact_response
from collections import Counter
from loguru import logger
from mcp.server.fastmcp import Context
from mcp.types import TextContent
from pydantic import Field
from typing import Any, Dict, Optional, List


class EKSSecurityHandler:
    """Handler for EKS security checks in the EKS MCP Server."""

    def __init__(self, mcp, client_cache):
        """Initialize the EKS security handler.

        Args:
            mcp: The MCP server instance
            client_cache: K8sClientCache instance to share between handlers
        """
        self.mcp = mcp
        self.client_cache = client_cache
        self.check_registry = self._load_check_registry()

        # Register the comprehensive check tool
        self.mcp.tool(name='check_eks_security')(self.check_eks_security)

    def _load_check_registry(self) -> Dict[str, Any]:
        """Load check definitions from JSON file."""
        try:
            config_path = Path(__file__).parent / 'data' / 'eks_security_checks.json'
            with open(config_path, 'r') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to load check registry: {e}")
            return {}

    def _get_all_checks(self) -> Dict[str, Dict[str, Any]]:
        """Get all checks flattened into a single dictionary."""
        all_checks = {}
        for category in ['iam_checks', 'pod_security', 'multi_tenancy', 'detective_controls', 'data_encryption_and_secrets_mgmt', 'infra_security']:
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

    def _create_error_response(self, cluster_name: str, error_msg: str) -> SecurityCheckResponse:
        """Create an error response."""
        return SecurityCheckResponse(
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

    async def check_eks_security(
        self,
        ctx: Context,
        cluster_name: str = Field(
            ..., description='Name of the EKS cluster to check for security best practices.'
        ),
        namespace: Optional[str] = Field(
            None, description='Optional namespace to limit the check scope.'
        ),
        region: str = Field(
            ..., description='AWS region where the cluster is located (required).'
        ),
    ) -> SecurityCheckResponse:
        """Check EKS cluster for security best practices.

        This tool runs a comprehensive set of security checks against your EKS cluster
        to identify potential security issues and provides remediation guidance.

        The tool evaluates critical security best practices across IAM and access control:
        - IAM Related Checks: Cluster access management, endpoint security, and authentication
        """
        try:
            logger.info(f'Starting security check for cluster: {cluster_name}')

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
            summary = f'Cluster {cluster_name} security check: {passed_count} checks passed, {failed_count} checks failed'

            # Create detailed response with JSON data in content
            content_text = json.dumps(compact_response(summary, check_results), separators=(',', ':'))

            return SecurityCheckResponse(
                isError=False,
                content=[TextContent(type='text', text=content_text)],
                check_results=check_results,
                overall_compliant=all_compliant,
                summary=summary,
            )

        except Exception as e:
            logger.error(f'Unexpected error in security check: {str(e)}')
            return self._create_error_response(cluster_name, str(e))

    async def _initialize_shared_data(self, k8s_client, cluster_name: str, namespace: Optional[str], region: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """Initialize shared data once to avoid redundant API calls (optimization)."""
        try:
            shared_data = {}
            
            # Initialize AWS clients once using AwsHelper (region auto-detected from environment)
            shared_data['eks_client'] = AwsHelper.create_boto3_client('eks', region_name=region)
            shared_data['ec2_client'] = AwsHelper.create_boto3_client('ec2', region_name=region)
            
            # Fetch cluster info ONCE (used by I1, I2, I8, D1)
            try:
                response = shared_data['eks_client'].describe_cluster(name=cluster_name)
                shared_data['cluster_info'] = response['cluster']
                logger.info('Fetched cluster info once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch cluster info: {str(e)}')
                shared_data['cluster_info'] = {}
            
            # Fetch addons ONCE (used by I5, I8)
            try:
                addons = shared_data['eks_client'].list_addons(clusterName=cluster_name)
                shared_data['addons'] = addons.get('addons', [])
                logger.info(f'Fetched {len(shared_data["addons"])} addons once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch addons: {str(e)}')
                shared_data['addons'] = []
            
            # Fetch node groups ONCE (used by I6, IS1, IS2, IS3)
            try:
                node_groups = shared_data['eks_client'].list_nodegroups(clusterName=cluster_name)
                shared_data['nodegroups'] = node_groups.get('nodegroups', [])
                logger.info(f'Fetched {len(shared_data["nodegroups"])} node groups once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch node groups: {str(e)}')
                shared_data['nodegroups'] = []
            
            # Fetch pods ONCE (used by I7, P2, P3, P4, P5, P6, M3)
            try:
                if namespace:
                    pods = k8s_client.list_resources(kind='Pod', api_version='v1', namespace=namespace, field_selector='status.phase!=Succeeded,status.phase!=Failed')
                else:
                    pods = k8s_client.list_resources(kind='Pod', api_version='v1', field_selector='status.phase!=Succeeded,status.phase!=Failed')
                shared_data['pods'] = pods.items if hasattr(pods, 'items') else []
                logger.info(f'Fetched {len(shared_data["pods"])} pods once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch pods: {str(e)}')
                shared_data['pods'] = []
            
            # Single-pass pod security analysis (used by I7, P2, P3, P4, P5, P6)
            pod_flags = {'I7': [], 'P2': [], 'P3': [], 'P4': [], 'P5': [], 'P6': []}
            for pod in shared_data['pods']:
                try:
                    pkey = f"{pod.metadata.namespace}/{pod.metadata.name}"
                    
                    # P6: automountServiceAccountToken
                    automount = pod.spec.get('automountServiceAccountToken')
                    if automount is None or automount:
                        pod_flags['P6'].append(pkey)
                    
                    # P2: hostPath volumes
                    for vol in pod.spec.get('volumes', []):
                        if vol.get('hostPath') is not None:
                            pod_flags['P2'].append(pkey)
                            break
                    
                    # Pod-level security context
                    pod_sc = pod.spec.get('securityContext', {})
                    pod_run_as_non_root = pod_sc.get('runAsNonRoot') if pod_sc else None
                    pod_run_as_user = pod_sc.get('runAsUser') if pod_sc else None
                    
                    # I7: runs as root (pod-level check)
                    i7_compliant = False
                    if pod_run_as_non_root is True or (pod_run_as_user is not None and pod_run_as_user != 0):
                        i7_compliant = True
                    
                    # Iterate containers once for I7, P3, P4, P5
                    containers = pod.spec.get('containers', [])
                    has_mutable_tag = False
                    has_priv_esc = False
                    has_writable_fs = False
                    all_containers_non_root = True
                    
                    for c in containers:
                        # P3: mutable image tags
                        if not has_mutable_tag:
                            img = c.get('image', '')
                            if ':latest' in img or ':' not in img:
                                has_mutable_tag = True
                        
                        c_sc = c.get('securityContext', {})
                        
                        # P4: privilege escalation
                        if not has_priv_esc:
                            ape = c_sc.get('allowPrivilegeEscalation') if c_sc else None
                            if ape is None or ape:
                                has_priv_esc = True
                        
                        # P5: writable root filesystem
                        if not has_writable_fs:
                            rofs = c_sc.get('readOnlyRootFilesystem') if c_sc else None
                            if not rofs:
                                has_writable_fs = True
                        
                        # I7: container-level non-root (only if pod-level didn't pass)
                        if not i7_compliant and all_containers_non_root:
                            c_non_root = c_sc.get('runAsNonRoot') if c_sc else None
                            c_user = c_sc.get('runAsUser') if c_sc else None
                            if not (c_non_root is True or (c_user is not None and c_user != 0)):
                                all_containers_non_root = False
                    
                    if has_mutable_tag:
                        pod_flags['P3'].append(pkey)
                    if has_priv_esc:
                        pod_flags['P4'].append(pkey)
                    if has_writable_fs:
                        pod_flags['P5'].append(pkey)
                    if not i7_compliant and not (all_containers_non_root and containers):
                        pod_flags['I7'].append(pkey)
                except Exception:
                    pass
            
            shared_data['pod_flags'] = pod_flags
            logger.info(f'Pod analysis complete: I7={len(pod_flags["I7"])}, P2={len(pod_flags["P2"])}, '
                        f'P3={len(pod_flags["P3"])}, P4={len(pod_flags["P4"])}, '
                        f'P5={len(pod_flags["P5"])}, P6={len(pod_flags["P6"])}')
            
            # Fetch service accounts ONCE (used by I3, I8)
            try:
                if namespace:
                    service_accounts = k8s_client.list_resources(kind='ServiceAccount', api_version='v1', namespace=namespace)
                else:
                    service_accounts = k8s_client.list_resources(kind='ServiceAccount', api_version='v1')
                shared_data['service_accounts'] = service_accounts.items if hasattr(service_accounts, 'items') else []
                logger.info(f'Fetched {len(shared_data["service_accounts"])} service accounts once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch service accounts: {str(e)}')
                shared_data['service_accounts'] = []
            
            # Fetch namespaces ONCE (used by P1, M2)
            try:
                if namespace:
                    namespaces = k8s_client.list_resources(kind='Namespace', api_version='v1', namespace=namespace)
                else:
                    namespaces = k8s_client.list_resources(kind='Namespace', api_version='v1')
                shared_data['namespaces'] = namespaces.items if hasattr(namespaces, 'items') else []
                logger.info(f'Fetched {len(shared_data["namespaces"])} namespaces once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch namespaces: {str(e)}')
                shared_data['namespaces'] = []
            
            # Fetch nodes ONCE (used by M3)
            try:
                nodes = k8s_client.list_resources(kind='Node', api_version='v1')
                shared_data['nodes'] = nodes.items if hasattr(nodes, 'items') else []
                logger.info(f'Fetched {len(shared_data["nodes"])} nodes once for sharing')
            except Exception as e:
                logger.warning(f'Failed to fetch nodes: {str(e)}')
                shared_data['nodes'] = []
            
            # Store k8s_client for checks that need it
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
            'I1': self._check_cluster_access_manager,
            'I2': self._check_private_endpoint,
            'I3': self._check_service_account_tokens,
            'I4': self._check_least_privileged_rbac,
            'I5': self._check_pod_identity,
            'I6': self._check_imdsv2_enforcement,
            'I7': self._check_non_root_user,
            'I8': self._check_irsa_configuration,
            'P1': self._check_pod_security_standards,
            'P2': self._check_hostpath_usage,
            'P3': self._check_image_tags,
            'P4': self._check_privilege_escalation,
            'P5': self._check_readonly_filesystem,
            'P6': self._check_serviceaccount_token_mount,
            'M1': self._check_network_policies,
            'M2': self._check_namespace_quotas,
            'M3': self._check_node_isolation,
            'D1': self._check_control_plane_logs,
            'DE1': self._check_storage_encryption,
            'DE2': self._check_external_secrets,
            'IS1': self._check_private_subnets,
            'IS2': self._check_container_optimized_os,
            'IS3': self._check_worker_node_access,
            'DE3': self._check_kms_secrets_encryption,
            'I9': self._check_anonymous_bindings,
            'RS1': self._check_policy_enforcement_engine,
        }
        
        method = check_methods.get(check_id)
        if method:
            return await method(shared_data, cluster_name, namespace)
        else:
            return self._create_check_error_result(check_id, f'Check method not implemented for {check_id}')

    async def _check_cluster_access_manager(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if EKS Cluster Access Manager is configured."""
        try:
            # Use shared cluster info (optimization)
            cluster = shared_data.get('cluster_info', {})
            
            # Check authentication mode
            access_config = cluster.get('accessConfig', {})
            auth_mode = access_config.get('authenticationMode', 'CONFIG_MAP')
            
            if auth_mode in ['API', 'API_AND_CONFIG_MAP']:
                return self._create_check_result(
                    'I1',
                    True,
                    [],
                    f'Cluster uses {auth_mode} authentication mode'
                )
            else:
                return self._create_check_result(
                    'I1',
                    False,
                    [cluster_name],
                    f'Cluster uses {auth_mode} authentication mode, should use API or API_AND_CONFIG_MAP'
                )
        except Exception as e:
            return self._create_check_error_result('I1', str(e))

    async def _check_private_endpoint(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if EKS cluster endpoint is private."""
        try:
            # Use shared cluster info (optimization)
            cluster = shared_data.get('cluster_info', {})
            
            # Check endpoint configuration
            vpc_config = cluster.get('resourcesVpcConfig', {})
            public_access = vpc_config.get('endpointPublicAccess', True)
            private_access = vpc_config.get('endpointPrivateAccess', False)
            
            if not public_access and private_access:
                return self._create_check_result(
                    'I2',
                    True,
                    [],
                    'Cluster endpoint is private only'
                )
            elif public_access and private_access:
                return self._create_check_result(
                    'I2',
                    False,
                    [cluster_name],
                    'Cluster endpoint allows both public and private access'
                )
            else:
                return self._create_check_result(
                    'I2',
                    False,
                    [cluster_name],
                    'Cluster endpoint is public only'
                )
        except Exception as e:
            return self._create_check_error_result('I2', str(e))

    async def _check_service_account_tokens(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check for service account token usage."""
        try:
            # Use shared service accounts (optimization)
            service_accounts = shared_data.get('service_accounts', [])
            
            non_compliant_sa = []
            for sa in service_accounts:
                # Check if automountServiceAccountToken is explicitly set to True or not set (defaults to True)
                automount = sa.get('automountServiceAccountToken')
                if automount is None or automount:
                    sa_name = sa.metadata.name
                    sa_namespace = sa.metadata.namespace
                    non_compliant_sa.append(f"{sa_namespace}/{sa_name}")
            
            if non_compliant_sa:
                return self._create_check_result(
                    'I3',
                    False,
                    non_compliant_sa,
                    f'Found {len(non_compliant_sa)} service accounts with automountServiceAccountToken enabled'
                )
            else:
                return self._create_check_result(
                    'I3',
                    True,
                    [],
                    'All service accounts have automountServiceAccountToken disabled'
                )
                
        except Exception as e:
            return self._create_check_error_result('I3', str(e))

    async def _check_least_privileged_rbac(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check for overly permissive RoleBindings and ClusterRoleBindings."""
        try:
            # Check ClusterRoles and Roles for wildcard permissions
            k8s_client = shared_data.get('k8s_client')
            cluster_roles = k8s_client.list_resources(kind='ClusterRole', api_version='rbac.authorization.k8s.io/v1')
            roles = k8s_client.list_resources(kind='Role', api_version='rbac.authorization.k8s.io/v1', namespace=namespace) if namespace else k8s_client.list_resources(kind='Role', api_version='rbac.authorization.k8s.io/v1')
            
            overly_permissive = []
            all_roles = []
            
            if cluster_roles and hasattr(cluster_roles, 'items'):
                all_roles.extend(cluster_roles.items)
            if roles and hasattr(roles, 'items'):
                all_roles.extend(roles.items)
            
            for role in all_roles:
                if not role or not hasattr(role, 'metadata'):
                    continue
                    
                role_name = role.metadata.name
                role_namespace = getattr(role.metadata, 'namespace', 'cluster-wide')
                rules = getattr(role, 'rules', None) or []
                
                for rule in rules:
                    if not rule:
                        continue
                    verbs = rule.get('verbs', []) or []
                    resources = rule.get('resources', []) or []
                    api_groups = rule.get('apiGroups', []) or []
                    
                    if '*' in verbs or '*' in resources or '*' in api_groups:
                        overly_permissive.append(f"{role_namespace}/{role_name}")
                        break
            
            if overly_permissive:
                return self._create_check_result(
                    'I4',
                    False,
                    overly_permissive,
                    f'Found {len(overly_permissive)} roles with wildcard permissions'
                )
            else:
                return self._create_check_result(
                    'I4',
                    True,
                    [],
                    'All roles follow least privilege principle'
                )
        except Exception as e:
            return self._create_check_error_result('I4', str(e))

    async def _check_pod_identity(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if EKS Pod Identity is configured."""
        try:
            # Use shared addons (optimization)
            addons = shared_data.get('addons', [])
            
            # Check if pod identity agent addon is installed
            try:
                pod_identity_addon = 'eks-pod-identity-agent' in addons
                
                if pod_identity_addon:
                    return self._create_check_result(
                        'I5',
                        True,
                        [],
                        'EKS Pod Identity agent addon is installed'
                    )
                else:
                    return self._create_check_result(
                        'I5',
                        False,
                        [cluster_name],
                        'EKS Pod Identity agent addon is not installed'
                    )
            except Exception as e:
                return self._create_check_error_result('I5', str(e))
        except Exception as e:
            return self._create_check_error_result('I5', str(e))

    async def _check_imdsv2_enforcement(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if IMDSv2 is enforced on worker nodes."""
        try:
            # Use shared clients and node groups (optimization)
            ec2_client = shared_data.get('ec2_client')
            eks_client = shared_data.get('eks_client')
            node_groups_list = shared_data.get('nodegroups', [])
            
            non_compliant_instances = []
            
            for ng_name in node_groups_list:
                ng_details = eks_client.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)
                
                # Check launch template or instance configuration
                launch_template = ng_details['nodegroup'].get('launchTemplate')
                if launch_template:
                    lt_response = ec2_client.describe_launch_template_versions(
                        LaunchTemplateId=launch_template['id'],
                        Versions=[launch_template.get('version', '$Latest')]
                    )
                    
                    for version in lt_response['LaunchTemplateVersions']:
                        metadata_options = version.get('LaunchTemplateData', {}).get('MetadataOptions', {})
                        if metadata_options.get('HttpTokens') != 'required':
                            non_compliant_instances.append(f"nodegroup/{ng_name}")
            
            if non_compliant_instances:
                return self._create_check_result(
                    'I6',
                    False,
                    non_compliant_instances,
                    f'Found {len(non_compliant_instances)} node groups without IMDSv2 enforcement'
                )
            else:
                return self._create_check_result(
                    'I6',
                    True,
                    [],
                    'All node groups enforce IMDSv2'
                )
        except Exception as e:
            return self._create_check_error_result('I6', str(e))

    async def _check_non_root_user(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if pods run as non-root user."""
        try:
            pods = shared_data.get('pods', [])
            root_pods = shared_data.get('pod_flags', {}).get('I7', [])
            aggregated = _aggregate_by_owner(pods, root_pods)
            
            if root_pods:
                return self._create_check_result(
                    'I7', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(root_pods)} pods) running as root user'
                )
            return self._create_check_result('I7', True, [], 'All pods run as non-root user')
        except Exception as e:
            return self._create_check_error_result('I7', str(e))

    async def _check_irsa_configuration(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if IRSA is configured when Pod Identity is not available."""
        try:
            # Use shared addons and cluster info (optimization)
            addons = shared_data.get('addons', [])
            pod_identity_enabled = 'eks-pod-identity-agent' in addons
            
            if pod_identity_enabled:
                return self._create_check_result(
                    'I8',
                    True,
                    [],
                    'Pod Identity is enabled, IRSA check not required'
                )
            
            # Use shared cluster info (optimization)
            cluster_info = shared_data.get('cluster_info', {})
            oidc_issuer = cluster_info.get('identity', {}).get('oidc', {}).get('issuer')
            
            if not oidc_issuer:
                return self._create_check_result(
                    'I8',
                    False,
                    [cluster_name],
                    'OIDC identity provider is not configured'
                )
            
            # Use shared service accounts (optimization)
            service_accounts = shared_data.get('service_accounts', [])
            
            irsa_configured_sa = []
            for sa in service_accounts:
                annotations = sa.metadata.get('annotations', {})
                if annotations and annotations.get('eks.amazonaws.com/role-arn'):
                    sa_name = sa.metadata.name
                    sa_namespace = sa.metadata.namespace
                    irsa_configured_sa.append(f"{sa_namespace}/{sa_name}")
            
            if irsa_configured_sa:
                return self._create_check_result(
                    'I8',
                    True,
                    irsa_configured_sa,
                    f'Found {len(irsa_configured_sa)} service accounts with IRSA configured'
                )
            else:
                return self._create_check_result(
                    'I8',
                    False,
                    [],
                    'No service accounts found with IRSA configuration'
                )
        except Exception as e:
            return self._create_check_error_result('I8', str(e))

    async def _check_pod_security_standards(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if Pod Security Standards (PSS) and Pod Security Admission (PSA) is configured."""
        try:
            # Use shared namespaces (optimization)
            namespaces = shared_data.get('namespaces', [])
            
            non_compliant_ns = []
            psa_labels = ['pod-security.kubernetes.io/enforce', 'pod-security.kubernetes.io/audit', 'pod-security.kubernetes.io/warn']
            
            for ns in namespaces:
                ns_name = ns.metadata.name
                labels = ns.metadata.get('labels', {})
                
                # Check if any PSA labels are present
                has_psa = any(labels.get(label) is not None for label in psa_labels)
                if not has_psa and ns_name not in ['kube-system', 'kube-public', 'kube-node-lease']:
                    non_compliant_ns.append(ns_name)
            
            if non_compliant_ns:
                return self._create_check_result(
                    'P1',
                    False,
                    non_compliant_ns,
                    f'Found {len(non_compliant_ns)} namespaces without Pod Security Standards configured'
                )
            else:
                return self._create_check_result(
                    'P1',
                    True,
                    [],
                    'All namespaces have Pod Security Standards configured'
                )
        except Exception as e:
            return self._create_check_error_result('P1', str(e))

    async def _check_hostpath_usage(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check for hostPath volume usage."""
        try:
            pods = shared_data.get('pods', [])
            hostpath_pods = shared_data.get('pod_flags', {}).get('P2', [])
            aggregated = _aggregate_by_owner(pods, hostpath_pods)
            
            if hostpath_pods:
                return self._create_check_result(
                    'P2', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(hostpath_pods)} pods) using hostPath volumes'
                )
            return self._create_check_result('P2', True, [], 'No pods using hostPath volumes')
        except Exception as e:
            return self._create_check_error_result('P2', str(e))

    async def _check_image_tags(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if pods are using latest or mutable image tags."""
        try:
            pods = shared_data.get('pods', [])
            mutable_tag_pods = shared_data.get('pod_flags', {}).get('P3', [])
            aggregated = _aggregate_by_owner(pods, mutable_tag_pods)
            
            if mutable_tag_pods:
                return self._create_check_result(
                    'P3', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(mutable_tag_pods)} pods) using mutable image tags'
                )
            return self._create_check_result('P3', True, [], 'All pods use immutable image tags')
        except Exception as e:
            return self._create_check_error_result('P3', str(e))

    async def _check_privilege_escalation(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check for privilege escalation in pods."""
        try:
            pods = shared_data.get('pods', [])
            priv_esc_pods = shared_data.get('pod_flags', {}).get('P4', [])
            aggregated = _aggregate_by_owner(pods, priv_esc_pods)
            
            if priv_esc_pods:
                return self._create_check_result(
                    'P4', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(priv_esc_pods)} pods) allowing privilege escalation'
                )
            return self._create_check_result('P4', True, [], 'All pods have privilege escalation disabled')
        except Exception as e:
            return self._create_check_error_result('P4', str(e))

    async def _check_readonly_filesystem(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if pods have read-only root filesystem."""
        try:
            pods = shared_data.get('pods', [])
            writable_fs_pods = shared_data.get('pod_flags', {}).get('P5', [])
            aggregated = _aggregate_by_owner(pods, writable_fs_pods)
            
            if writable_fs_pods:
                return self._create_check_result(
                    'P5', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(writable_fs_pods)} pods) with writable root filesystem'
                )
            return self._create_check_result('P5', True, [], 'All pods have read-only root filesystem')
        except Exception as e:
            return self._create_check_error_result('P5', str(e))

    async def _check_serviceaccount_token_mount(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if ServiceAccount token mounting is disabled for pods."""
        try:
            pods = shared_data.get('pods', [])
            token_mount_pods = shared_data.get('pod_flags', {}).get('P6', [])
            aggregated = _aggregate_by_owner(pods, token_mount_pods)
            
            if token_mount_pods:
                return self._create_check_result(
                    'P6', False, aggregated,
                    f'Found {len(aggregated)} workloads ({len(token_mount_pods)} pods) with ServiceAccount token mounting enabled'
                )
            return self._create_check_result('P6', True, [], 'All pods have ServiceAccount token mounting disabled')
        except Exception as e:
            return self._create_check_error_result('P6', str(e))

    async def _check_network_policies(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if Network Policies are used to restrict communication between namespaces."""
        try:
            k8s_client = shared_data.get('k8s_client')
            if namespace:
                network_policies = k8s_client.list_resources(kind='NetworkPolicy', api_version='networking.k8s.io/v1', namespace=namespace)
            else:
                network_policies = k8s_client.list_resources(kind='NetworkPolicy', api_version='networking.k8s.io/v1')
            
            if network_policies and hasattr(network_policies, 'items') and len(network_policies.items) > 0:
                policy_count = len(network_policies.items)
                policy_names = [f"{policy.metadata.namespace}/{policy.metadata.name}" for policy in network_policies.items]
                return self._create_check_result(
                    'M1',
                    True,
                    policy_names,
                    f'Found {policy_count} Network Policies configured for network isolation'
                )
            else:
                return self._create_check_result(
                    'M1',
                    False,
                    [],
                    'No Network Policies found - namespaces can communicate freely'
                )
        except Exception as e:
            return self._create_check_error_result('M1', str(e))

    async def _check_namespace_quotas(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if Resource Quotas are defined at the namespace level."""
        try:
            k8s_client = shared_data.get('k8s_client')
            if namespace:
                resource_quotas = k8s_client.list_resources(kind='ResourceQuota', api_version='v1', namespace=namespace)
                namespaces_to_check = [namespace]
            else:
                resource_quotas = k8s_client.list_resources(kind='ResourceQuota', api_version='v1')
                # Use shared namespaces (optimization)
                namespaces = shared_data.get('namespaces', [])
                namespaces_to_check = [ns.metadata.name for ns in namespaces if ns.metadata.name not in ['kube-system', 'kube-public', 'kube-node-lease']]
            
            namespaces_with_quotas = set()
            if resource_quotas and hasattr(resource_quotas, 'items'):
                for quota in resource_quotas.items:
                    namespaces_with_quotas.add(quota.metadata.namespace)
            
            namespaces_without_quotas = [ns for ns in namespaces_to_check if ns not in namespaces_with_quotas]
            
            if namespaces_without_quotas:
                return self._create_check_result(
                    'M2',
                    False,
                    namespaces_without_quotas,
                    f'Found {len(namespaces_without_quotas)} namespaces without Resource Quotas'
                )
            else:
                return self._create_check_result(
                    'M2',
                    True,
                    list(namespaces_with_quotas),
                    f'All {len(namespaces_with_quotas)} namespaces have Resource Quotas configured'
                )
        except Exception as e:
            return self._create_check_error_result('M2', str(e))

    async def _check_node_isolation(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if tenant workloads are isolated to specific nodes using taints/tolerations and node affinity."""
        try:
            # Use shared nodes (optimization)
            nodes = shared_data.get('nodes', [])
            tainted_nodes = []
            
            for node in nodes:
                node_name = node.metadata.name
                taints = node.spec.get('taints', [])
                if taints:
                    tainted_nodes.append(node_name)
            
            # Use shared pods (optimization)
            pods = shared_data.get('pods', [])
            
            isolated_pods = []
            for pod in pods:
                pod_name = pod.metadata.name
                pod_namespace = pod.metadata.namespace
                
                # Check for tolerations
                tolerations = pod.spec.get('tolerations', [])
                # Check for node affinity
                affinity = pod.spec.get('affinity', {})
                node_affinity = affinity.get('nodeAffinity', {})
                
                if tolerations or node_affinity:
                    isolated_pods.append(f"{pod_namespace}/{pod_name}")
            
            if tainted_nodes or isolated_pods:
                details = f'Found {len(tainted_nodes)} tainted nodes and {len(isolated_pods)} pods with isolation configuration'
                return self._create_check_result(
                    'M3',
                    True,
                    tainted_nodes + isolated_pods,
                    details
                )
            else:
                return self._create_check_result(
                    'M3',
                    False,
                    [],
                    'No node isolation mechanisms found (no taints, tolerations, or node affinity)'
                )
        except Exception as e:
            return self._create_check_error_result('M3', str(e))

    async def _check_control_plane_logs(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if EKS Control Plane logs are enabled."""
        try:
            # Use shared cluster info (optimization)
            cluster = shared_data.get('cluster_info', {})
            
            # Check logging configuration
            logging_config = cluster.get('logging', {})
            cluster_logging = logging_config.get('clusterLogging', [])
            
            enabled_log_types = []
            disabled_log_types = []
            
            for log_config in cluster_logging:
                log_types = log_config.get('types', [])
                enabled = log_config.get('enabled', False)
                
                if enabled:
                    enabled_log_types.extend(log_types)
                else:
                    disabled_log_types.extend(log_types)
            
            # All possible log types
            all_log_types = ['api', 'audit', 'authenticator', 'controllerManager', 'scheduler']
            
            if len(enabled_log_types) == len(all_log_types):
                return self._create_check_result(
                    'D1',
                    True,
                    enabled_log_types,
                    f'All control plane log types are enabled: {enabled_log_types}'
                )
            elif enabled_log_types:
                return self._create_check_result(
                    'D1',
                    False,
                    enabled_log_types,
                    f'Partial control plane logging enabled: {enabled_log_types}, missing: {[t for t in all_log_types if t not in enabled_log_types]}'
                )
            else:
                return self._create_check_result(
                    'D1',
                    False,
                    [],
                    'No control plane logs are enabled'
                )
        except Exception as e:
            return self._create_check_error_result('D1', str(e))

    async def _check_storage_encryption(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if encryption is enabled in StorageClass."""
        try:
            # Get all StorageClasses
            k8s_client = shared_data.get('k8s_client')
            storage_classes = k8s_client.list_resources(kind='StorageClass', api_version='storage.k8s.io/v1')
            
            non_encrypted_sc = []
            encrypted_sc = []
            
            for sc in storage_classes.items:
                sc_name = sc.metadata.name
                parameters = sc.get('parameters', {})
                
                # Check for encryption parameters based on provisioner
                provisioner = sc.get('provisioner', '')
                encrypted = False
                
                # provisioner is the StorageClass CSI driver name (an exact
                # identifier such as "ebs.csi.aws.com"), not a URL or path —
                # match it exactly. (Exact match also satisfies CodeQL's
                # py/incomplete-url-substring-sanitization, which misreads
                # these host-like driver names as URLs.)
                if provisioner == 'ebs.csi.aws.com':
                    # EBS CSI driver - check for encrypted parameter
                    encrypted = parameters.get('encrypted', '').lower() == 'true'
                elif provisioner == 'efs.csi.aws.com':
                    # EFS CSI driver - EFS is encrypted by default in newer versions
                    encrypted = True
                elif provisioner == 'fsx.csi.aws.com':
                    # FSx CSI driver - check for encryption parameters
                    encrypted = parameters.get('KmsKeyId') is not None or parameters.get('EncryptionAtTransitRequested') is not None
                
                if encrypted:
                    encrypted_sc.append(sc_name)
                else:
                    non_encrypted_sc.append(sc_name)
            
            if non_encrypted_sc:
                return self._create_check_result(
                    'DE1',
                    False,
                    non_encrypted_sc,
                    f'Found {len(non_encrypted_sc)} StorageClasses without encryption enabled'
                )
            elif encrypted_sc:
                return self._create_check_result(
                    'DE1',
                    True,
                    encrypted_sc,
                    f'All {len(encrypted_sc)} StorageClasses have encryption enabled'
                )
            else:
                return self._create_check_result(
                    'DE1',
                    False,
                    [],
                    'No StorageClasses found in the cluster'
                )
        except Exception as e:
            return self._create_check_error_result('DE1', str(e))

    async def _check_external_secrets(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if external secrets provider is used."""
        try:
            k8s_client = shared_data.get('k8s_client')
            # Check for External Secrets Operator
            external_secrets_found = []
            
            # Check for ExternalSecret CRDs
            try:
                if namespace:
                    external_secrets = k8s_client.list_resources(kind='ExternalSecret', api_version='external-secrets.io/v1beta1', namespace=namespace)
                else:
                    external_secrets = k8s_client.list_resources(kind='ExternalSecret', api_version='external-secrets.io/v1beta1')
                
                if external_secrets and hasattr(external_secrets, 'items') and len(external_secrets.items) > 0:
                    for es in external_secrets.items:
                        external_secrets_found.append(f"{es.metadata.namespace}/{es.metadata.name}")
            except Exception:
                # ExternalSecret CRD might not be installed
                pass
            
            # Check for AWS Secrets Manager CSI driver
            try:
                if namespace:
                    secret_provider_classes = k8s_client.list_resources(kind='SecretProviderClass', api_version='secrets-store.csi.x-k8s.io/v1', namespace=namespace)
                else:
                    secret_provider_classes = k8s_client.list_resources(kind='SecretProviderClass', api_version='secrets-store.csi.x-k8s.io/v1')
                
                if secret_provider_classes and hasattr(secret_provider_classes, 'items'):
                    for spc in secret_provider_classes.items:
                        spec = spc.get('spec', {})
                        provider = spec.get('provider', '')
                        if provider == 'aws':
                            external_secrets_found.append(f"{spc.metadata.namespace}/{spc.metadata.name}")
            except Exception:
                # SecretProviderClass CRD might not be installed
                pass
            
            # Check for AWS Load Balancer Controller (uses external secrets)
            try:
                if namespace:
                    deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1', namespace=namespace)
                else:
                    deployments = k8s_client.list_resources(kind='Deployment', api_version='apps/v1')
                
                for deployment in deployments.items:
                    dep_name = deployment.metadata.name
                    if 'external-secrets' in dep_name.lower() or 'secrets-store-csi' in dep_name.lower():
                        external_secrets_found.append(f"{deployment.metadata.namespace}/{dep_name}")
            except Exception:
                pass
            
            if external_secrets_found:
                return self._create_check_result(
                    'DE2',
                    True,
                    external_secrets_found,
                    f'Found {len(external_secrets_found)} external secrets resources'
                )
            else:
                return self._create_check_result(
                    'DE2',
                    False,
                    [],
                    'No external secrets provider found - using native Kubernetes secrets only'
                )
        except Exception as e:
            return self._create_check_error_result('DE2', str(e))

    async def _check_private_subnets(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if worker nodes are deployed onto private subnets."""
        try:
            # Use shared clients and node groups (optimization)
            ec2_client = shared_data.get('ec2_client')
            eks_client = shared_data.get('eks_client')
            node_groups_list = shared_data.get('nodegroups', [])
            public_nodegroups = []
            private_nodegroups = []
            
            for ng_name in node_groups_list:
                ng_details = eks_client.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)
                subnets = ng_details['nodegroup'].get('subnets', [])
                
                # Check if subnets are private
                for subnet_id in subnets:
                    subnet_response = ec2_client.describe_subnets(SubnetIds=[subnet_id])
                    subnet = subnet_response['Subnets'][0]
                    
                    # Check route table for internet gateway
                    route_tables = ec2_client.describe_route_tables(
                        Filters=[{'Name': 'association.subnet-id', 'Values': [subnet_id]}]
                    )
                    
                    is_public = False
                    for rt in route_tables['RouteTables']:
                        for route in rt.get('Routes', []):
                            if route.get('GatewayId', '').startswith('igw-'):
                                is_public = True
                                break
                    
                    if is_public:
                        public_nodegroups.append(ng_name)
                        break
                else:
                    private_nodegroups.append(ng_name)
            
            if public_nodegroups:
                return self._create_check_result(
                    'IS1',
                    False,
                    public_nodegroups,
                    f'Found {len(public_nodegroups)} node groups in public subnets'
                )
            else:
                return self._create_check_result(
                    'IS1',
                    True,
                    private_nodegroups,
                    f'All {len(private_nodegroups)} node groups are in private subnets'
                )
        except Exception as e:
            return self._create_check_error_result('IS1', str(e))

    async def _check_container_optimized_os(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if managed node groups use container-optimized OS.
        
        Note: This check only applies to EKS managed node groups.
        Self-managed and Karpenter-managed nodes are not evaluated
        as they don't have an amiType field in the EKS API.
        """
        try:
            # Use shared clients and node groups (optimization)
            eks_client = shared_data.get('eks_client')
            node_groups_list = shared_data.get('nodegroups', [])
            
            if not node_groups_list:
                return self._create_check_result(
                    'IS2',
                    True,
                    [],
                    'No EKS managed node groups found (self-managed/Karpenter nodes are not evaluated by this check)'
                )
            
            optimized_nodegroups = []
            non_optimized_nodegroups = []
            
            for ng_name in node_groups_list:
                ng_details = eks_client.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)
                
                # Check AMI type
                ami_type = ng_details['nodegroup'].get('amiType', '')
                
                # Only CUSTOM AMIs are non-optimized; all AWS-provided AMI types are container-optimized
                if ami_type and ami_type != 'CUSTOM':
                    optimized_nodegroups.append(ng_name)
                else:
                    non_optimized_nodegroups.append(ng_name)
            
            if non_optimized_nodegroups:
                return self._create_check_result(
                    'IS2',
                    False,
                    non_optimized_nodegroups,
                    f'Found {len(non_optimized_nodegroups)} managed node groups using custom (non-optimized) AMIs'
                )
            else:
                return self._create_check_result(
                    'IS2',
                    True,
                    optimized_nodegroups,
                    f'All {len(optimized_nodegroups)} managed node groups use container-optimized OS'
                )
        except Exception as e:
            return self._create_check_error_result('IS2', str(e))

    async def _check_worker_node_access(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check if worker nodes have minimal access (no SSH, use SSM)."""
        try:
            # Use shared clients and node groups (optimization)
            eks_client = shared_data.get('eks_client')
            node_groups_list = shared_data.get('nodegroups', [])
            ssh_enabled_nodegroups = []
            secure_nodegroups = []
            
            for ng_name in node_groups_list:
                ng_details = eks_client.describe_nodegroup(clusterName=cluster_name, nodegroupName=ng_name)
                
                # Check if SSH key is configured
                remote_access = ng_details['nodegroup'].get('remoteAccess', {})
                ec2_ssh_key = remote_access.get('ec2SshKey')
                
                if ec2_ssh_key:
                    ssh_enabled_nodegroups.append(ng_name)
                else:
                    secure_nodegroups.append(ng_name)
            
            if ssh_enabled_nodegroups:
                return self._create_check_result(
                    'IS3',
                    False,
                    ssh_enabled_nodegroups,
                    f'Found {len(ssh_enabled_nodegroups)} node groups with SSH access enabled'
                )
            else:
                return self._create_check_result(
                    'IS3',
                    True,
                    secure_nodegroups,
                    f'All {len(secure_nodegroups)} node groups have SSH access disabled'
                )
        except Exception as e:
            return self._create_check_error_result('IS3', str(e))

    async def _check_kms_secrets_encryption(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check DE3: Verify envelope encryption is enabled for Kubernetes Secrets using KMS."""
        try:
            # Reuse cluster_info already fetched in _initialize_shared_data
            cluster_info = shared_data.get('cluster_info', {})
            encryption_config = cluster_info.get('encryptionConfig', [])

            secrets_encrypted = False
            kms_key_arn = None

            for config in encryption_config:
                resources = config.get('resources', [])
                if 'secrets' in resources:
                    provider = config.get('provider', {})
                    kms_key_arn = provider.get('keyArn')
                    if kms_key_arn:
                        secrets_encrypted = True
                        break

            if secrets_encrypted:
                return self._create_check_result(
                    'DE3',
                    True,
                    [],
                    f'Kubernetes Secrets envelope encryption is enabled with KMS key: {kms_key_arn}'
                )
            else:
                return self._create_check_result(
                    'DE3',
                    False,
                    [cluster_name],
                    'Kubernetes Secrets envelope encryption is not enabled. Secrets are stored in etcd without KMS encryption.'
                )
        except Exception as e:
            return self._create_check_error_result('DE3', str(e))

    async def _check_anonymous_bindings(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check I9: Ensure no ClusterRoleBindings grant access to anonymous or unauthenticated groups."""
        try:
            k8s_client = shared_data.get('k8s_client')
            cluster_role_bindings = k8s_client.list_resources(
                kind='ClusterRoleBinding', api_version='rbac.authorization.k8s.io/v1'
            )

            unsafe_subjects = {'system:anonymous', 'system:unauthenticated'}
            non_compliant_bindings = []

            for crb in cluster_role_bindings.items:
                crb_name = crb.metadata.name
                subjects = getattr(crb, 'subjects', None) or []

                for subject in subjects:
                    subject_name = subject.get('name', '')
                    if subject_name in unsafe_subjects:
                        non_compliant_bindings.append(f'{crb_name} (subject: {subject_name})')
                        break

            if non_compliant_bindings:
                return self._create_check_result(
                    'I9',
                    False,
                    non_compliant_bindings,
                    f'Found {len(non_compliant_bindings)} ClusterRoleBindings granting access to anonymous/unauthenticated subjects'
                )
            else:
                return self._create_check_result(
                    'I9',
                    True,
                    [],
                    'No ClusterRoleBindings grant access to anonymous or unauthenticated subjects'
                )
        except Exception as e:
            return self._create_check_error_result('I9', str(e))

    async def _check_policy_enforcement_engine(self, shared_data: Dict[str, Any], cluster_name: str, namespace: Optional[str]) -> Dict[str, Any]:
        """Check RS1: Detect if a policy enforcement engine (Kyverno, Gatekeeper/OPA) is deployed."""
        try:
            k8s_client = shared_data.get('k8s_client')
            engines_found = []

            # Check for Gatekeeper/OPA by looking for the constraints CRD API group
            try:
                k8s_client.api_client.call_api(
                    '/apis/constraints.gatekeeper.sh',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                engines_found.append('Gatekeeper (OPA)')
            except Exception:
                pass

            # Check for Kyverno by looking for its CRD API group
            try:
                k8s_client.api_client.call_api(
                    '/apis/kyverno.io/v1',
                    'GET',
                    auth_settings=['BearerToken'],
                    response_type='object',
                    _preload_content=False
                )
                engines_found.append('Kyverno')
            except Exception:
                pass

            if engines_found:
                return self._create_check_result(
                    'RS1',
                    True,
                    engines_found,
                    f'Policy enforcement engine detected: {", ".join(engines_found)}'
                )
            else:
                return self._create_check_result(
                    'RS1',
                    False,
                    [],
                    'No policy enforcement engine found (Kyverno, Gatekeeper/OPA). Consider deploying one to enforce security policies via admission control.'
                )
        except Exception as e:
            return self._create_check_error_result('RS1', str(e))



