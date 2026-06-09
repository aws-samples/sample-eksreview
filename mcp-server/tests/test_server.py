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

"""Tests for the server module of the eks-review-mcp-server."""

import pytest
from awslabs.eks_review_mcp_server.server import (
    mcp,
    resiliency_handler,
    security_handler,
    karpenter_handler,
    cluster_autoscaler_handler,
    networking_handler,
    client_cache,
)


class TestMCPServer:
    """Tests for the MCP server."""

    def test_mcp_initialization(self):
        """Test that the MCP server is initialized correctly."""
        assert mcp.name == 'awslabs.eks-review-mcp-server'
        assert mcp.instructions is not None
        assert 'Amazon EKS Review MCP Server' in mcp.instructions
        assert 'Output Format' in mcp.instructions
        assert 'AI Role' in mcp.instructions

    def test_mcp_dependencies(self):
        """Test that the MCP server has the required dependencies."""
        assert 'pydantic' in mcp.dependencies
        assert 'loguru' in mcp.dependencies
        assert 'boto3' in mcp.dependencies
        assert 'kubernetes' in mcp.dependencies
        assert 'cachetools' in mcp.dependencies

    def test_handlers_initialization(self):
        """Test that all handlers are initialized correctly."""
        assert resiliency_handler is not None
        assert security_handler is not None
        assert karpenter_handler is not None
        assert cluster_autoscaler_handler is not None
        assert networking_handler is not None

    def test_client_cache_initialization(self):
        """Test that the client cache is initialized correctly."""
        assert client_cache is not None

    def test_handlers_share_client_cache(self):
        """Test that all handlers share the same client cache instance."""
        assert resiliency_handler.client_cache is client_cache
        assert security_handler.client_cache is client_cache
        assert karpenter_handler.client_cache is client_cache
        assert cluster_autoscaler_handler.client_cache is client_cache
        assert networking_handler.client_cache is client_cache


class TestTools:
    """Tests for the MCP tools."""

    def test_check_eks_networking_registration(self):
        """Test that the check_eks_networking tool is registered correctly."""
        tool = mcp._tool_manager.get_tool('check_eks_networking')
        assert tool is not None
        assert tool.name == 'check_eks_networking'
        assert 'Check EKS cluster for networking best practices' in tool.description

    def test_check_eks_security_registration(self):
        """Test that the check_eks_security tool is registered correctly."""
        tool = mcp._tool_manager.get_tool('check_eks_security')
        assert tool is not None
        assert tool.name == 'check_eks_security'
        assert 'Check EKS cluster for security best practices' in tool.description

    def test_check_eks_resiliency_registration(self):
        """Test that the check_eks_resiliency tool is registered correctly."""
        tool = mcp._tool_manager.get_tool('check_eks_resiliency')
        assert tool is not None
        assert tool.name == 'check_eks_resiliency'
        assert 'Check EKS cluster for resiliency best practices' in tool.description

    def test_check_karpenter_best_practices_registration(self):
        """Test that the check_karpenter_best_practices tool is registered correctly."""
        tool = mcp._tool_manager.get_tool('check_karpenter_best_practices')
        assert tool is not None
        assert tool.name == 'check_karpenter_best_practices'
        assert 'Check EKS cluster for Karpenter best practices' in tool.description

    def test_check_cluster_autoscaler_best_practices_registration(self):
        """Test that the check_cluster_autoscaler_best_practices tool is registered correctly."""
        tool = mcp._tool_manager.get_tool('check_cluster_autoscaler_best_practices')
        assert tool is not None
        assert tool.name == 'check_cluster_autoscaler_best_practices'
        assert 'Check EKS cluster for Cluster Autoscaler best practices' in tool.description
