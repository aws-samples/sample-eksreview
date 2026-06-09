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

"""awslabs eks-review MCP Server implementation."""

from awslabs.eks_review_mcp_server.eks_resiliency_handler import EKSResiliencyHandler
from awslabs.eks_review_mcp_server.eks_security_handler import EKSSecurityHandler
from awslabs.eks_review_mcp_server.eks_karpenter_handler import EKSKarpenterHandler
from awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler import EKSClusterAutoscalerHandler
from awslabs.eks_review_mcp_server.eks_networking_handler import EKSNetworkingHandler
from awslabs.eks_review_mcp_server.eks_upgrade_handler import EKSUpgradeHandler
from awslabs.eks_review_mcp_server.eks_observability_handler import EKSObservabilityHandler
from awslabs.eks_review_mcp_server.k8s_client_cache import K8sClientCache
from loguru import logger
from mcp.server.fastmcp import FastMCP
from typing import Literal


INSTRUCTIONS = """Amazon EKS Review MCP Server — Assess EKS clusters against AWS best practices across security, resiliency, networking, autoscaling, observability, and upgrade readiness.

Always pass `region` for consistent behavior. Observability requires K8s 1.28+.

## Output Format

Review tools return compact JSON:
{"summary":"...","passed":["Check A"],"failed":[{"n":"Check C","s":"H","d":"details","r":{...}}]}

Upgrade readiness returns compact JSON with extra fields:
{"v":"1.30->1.31","blockers":2,"warnings":5,"passed":["U1:Check A"],"failed":[{"id":"U5","n":"Check C","s":"C","d":"details","r":[...],"t":"b"}]}

Field key reference:
- n = check name, s = severity (C/H/M/L), d = details, r = impacted resources
- id = check ID (upgrade only, e.g. U1, U5), t = timing (b=before, a=after upgrade)
- Passed checks: name-only strings (review) or "ID:Name" strings (upgrade)
- Resources grouped by namespace when >3: {"ns":["app-1","app-2"]} → full path is ns/app-1

## AI Role

Generate remediation from d and r fields. Prioritize C→H→M→L. Tools report state only — they do not modify the cluster.
"""

mcp = FastMCP(
    "awslabs.eks-review-mcp-server",
    instructions=INSTRUCTIONS,
    dependencies=[
        'pydantic',
        'loguru',
        'boto3',
        'kubernetes',
        'cachetools',
        'pyyaml',
    ],
)


# Initialize shared client cache
client_cache = K8sClientCache()

# Initialize the EKS resiliency handler
resiliency_handler = EKSResiliencyHandler(mcp, client_cache)

# Initialize the EKS security handler
security_handler = EKSSecurityHandler(mcp, client_cache)

# Initialize the EKS Karpenter handler
karpenter_handler = EKSKarpenterHandler(mcp, client_cache)

# Initialize the EKS Cluster Autoscaler handler
cluster_autoscaler_handler = EKSClusterAutoscalerHandler(mcp, client_cache)

# Initialize the EKS networking handler
networking_handler = EKSNetworkingHandler(mcp, client_cache)

# Initialize the EKS upgrade readiness handler
upgrade_handler = EKSUpgradeHandler(mcp, client_cache)

# Initialize the EKS observability handler
observability_handler = EKSObservabilityHandler(mcp, client_cache)


def main():
    """Run the MCP server with CLI argument support."""

    logger.trace('A trace message.')
    logger.debug('A debug message.')
    logger.info('An info message.')
    logger.success('A success message.')
    logger.warning('A warning message.')
    logger.error('An error message.')
    logger.critical('A critical message.')

    mcp.run()


if __name__ == '__main__':
    main()
