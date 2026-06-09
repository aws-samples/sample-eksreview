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

"""Data models for the EKS Review MCP Server."""

from mcp.types import CallToolResult
from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class ResiliencyCheckResponse(CallToolResult):
    """Response model for EKS resiliency check tool."""

    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')


class SecurityCheckResponse(CallToolResult):
    """Response model for EKS security check tool."""

    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')


class KarpenterCheckResponse(CallToolResult):
    """Response model for Karpenter best practices check tool."""
    
    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')


class ClusterAutoscalerCheckResponse(CallToolResult):
    """Response model for Cluster Autoscaler best practices check tool."""
    
    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')

    
class NetworkingCheckResponse(CallToolResult):
    """Response model for EKS networking check tool."""

    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')


class ObservabilityCheckResponse(CallToolResult):
    """Response model for EKS observability check tool."""

    check_results: List[Dict[str, Any]] = Field(..., description='List of check results')
    overall_compliant: bool = Field(..., description='Whether all checks passed')
    summary: str = Field(..., description='Summary of the check results')


class UpgradeCheckResponse(CallToolResult):
    """Response model for EKS upgrade readiness check tool."""

    check_results: List[Dict[str, Any]] = Field(..., description='List of upgrade readiness check results')
    overall_ready: bool = Field(..., description='Whether the cluster is ready to upgrade (no blockers)')
    blockers: int = Field(..., description='Count of required fixes that block the upgrade')
    warnings: int = Field(..., description='Count of recommended fixes')
    current_version: str = Field(..., description='Current cluster Kubernetes version')
    target_version: str = Field(..., description='Target Kubernetes version for upgrade')
    summary: str = Field(..., description='Summary of upgrade readiness assessment')