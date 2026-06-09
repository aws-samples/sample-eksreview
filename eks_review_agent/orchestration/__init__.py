"""Orchestration — sub-agent pipelines and MCP integration.

Two pipelines today (review, upgrade) share the
subagent_pipeline.run_subagent_pipeline runner. mcp.py manages the
stdio MCP client; mcp_checks.py runs the per-domain MCP tools that
feed the review pipeline.
"""
