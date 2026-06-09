"""MCP check execution — calls each review domain tool and collects raw results.

Returns raw JSON results per domain for downstream report compilation.
"""

import json
import logging
import time
import uuid

from eks_review_agent.core.spinner import Spinner

logger = logging.getLogger("eksreview")

DOMAIN_TO_MCP_TOOL = {
    "security": "check_eks_security",
    "resiliency": "check_eks_resiliency",
    "networking": "check_eks_networking",
    "karpenter": "check_karpenter_best_practices",
    "cluster-autoscaler": "check_cluster_autoscaler_best_practices",
    "observability": "check_eks_observability",
}

ALL_DOMAINS = list(DOMAIN_TO_MCP_TOOL.keys())


def _extract_tool_content(result) -> str:
    """Extract readable content from an MCPToolResult.

    MCPToolResult is a TypedDict with:
      - content: list of {text?, json?, image?, document?}
      - structuredContent: optional dict
      - status: "success" | "error"

    We extract text and JSON content into a single string.
    """
    parts = []

    # Check for structuredContent first (MCP JSON response)
    if isinstance(result, dict):
        sc = result.get("structuredContent")
        if sc:
            try:
                parts.append(json.dumps(sc, indent=2, default=str))
            except (TypeError, ValueError):
                parts.append(str(sc))

        # Then extract from content list
        for item in result.get("content", []):
            if isinstance(item, dict):
                if "text" in item:
                    parts.append(item["text"])
                if "json" in item:
                    try:
                        parts.append(json.dumps(item["json"], indent=2, default=str))
                    except (TypeError, ValueError):
                        parts.append(str(item["json"]))

    if parts:
        return "\n".join(parts)

    # Fallback: stringify the whole thing
    return str(result)


class _Spinner(Spinner):
    """Backwards-compatible alias kept for any external callers.

    Inherits the shared Spinner helper. Existing code in this module
    uses Spinner directly; this alias remains so a prior `from mcp_checks
    import _Spinner` continues to work.
    """
    pass


def _is_error_result(result) -> tuple[bool, str]:
    """Detect whether an MCP tool result represents an error.

    Returns (is_error, error_text). Handles two cases:
      1. MCP-level error: result dict has isError=True.
      2. Handler-level error: the tool returned a payload whose content
         text indicates a failure (e.g. credentials/region/cluster access),
         surfaced by the handlers' _create_error_response.
    """
    if not isinstance(result, dict):
        return False, ""

    # Case 1: explicit MCP-level error flag
    if result.get("isError"):
        text = ""
        for item in result.get("content", []):
            if isinstance(item, dict) and "text" in item:
                text += item["text"]
        return True, (text or "unknown error")

    return False, ""


def collect_check_results(mcp_client, cluster_name, region=None, domains=None):
    if domains is None:
        domains = ALL_DOMAINS

    tools_to_call = []
    for domain in domains:
        tool_name = DOMAIN_TO_MCP_TOOL.get(domain)
        if not tool_name:
            continue
        kwargs = {"cluster_name": cluster_name}
        if region:
            kwargs["region"] = region
        tools_to_call.append((domain, tool_name, kwargs))

    if not tools_to_call:
        return {"text": "No valid domains specified.", "ok_count": 0, "error_count": 0,
                "errors": {}, "total": 0}

    logger.info("Running %d MCP tools: %s", len(tools_to_call), ", ".join(d for d, _, _ in tools_to_call))
    print(f"\n  Reviewing {cluster_name} ({len(tools_to_call)} checks)...\n", flush=True)

    GREEN = "\033[32m"
    RED = "\033[31m"
    RESET = "\033[0m"

    start = time.monotonic()
    results = {}
    errors: dict[str, str] = {}

    for idx, (domain, tool_name, kwargs) in enumerate(tools_to_call, 1):
        spinner = Spinner(f"[{idx}/{len(tools_to_call)}] {domain}")
        spinner.start()
        tool_start = time.monotonic()
        try:
            tool_use_id = f"review_{domain}_{uuid.uuid4().hex[:8]}"
            logger.info("Calling MCP tool: %s with %s", tool_name, kwargs)
            # Apply session-wide rate limiting before issuing the call.
            from eks_review_agent.core.rate_limiter import get_rate_limiter, RateLimitExceeded
            try:
                get_rate_limiter().check_and_increment(tool_name)
            except RateLimitExceeded as rle:
                spinner.stop(f"  {RED}✗{RESET} {domain} (rate-limited)")
                results[domain] = {"status": "error", "result": str(rle)}
                errors[domain] = str(rle)
                continue
            result = mcp_client.call_tool_sync(tool_use_id, tool_name, kwargs)
            tool_elapsed = time.monotonic() - tool_start

            # Detect MCP error payloads that don't raise (e.g. missing
            # credentials/region, cluster access failures). These return
            # quickly with isError=True — previously shown misleadingly as ✓.
            is_err, err_text = _is_error_result(result)
            if is_err:
                logger.error("MCP tool %s returned error in %.1fs: %s", tool_name, tool_elapsed, err_text)
                spinner.stop(f"  {RED}✗{RESET} {domain} ({tool_elapsed:.0f}s)")
                results[domain] = {"status": "error", "result": err_text}
                errors[domain] = err_text
                continue

            logger.info("MCP tool %s completed in %.1fs", tool_name, tool_elapsed)
            spinner.stop(f"  {GREEN}✓{RESET} {domain} ({tool_elapsed:.0f}s)")
            results[domain] = {"status": "success", "result": _extract_tool_content(result)}
        except Exception as e:
            tool_elapsed = time.monotonic() - tool_start
            logger.error("MCP tool %s failed in %.1fs: %s", tool_name, tool_elapsed, e)
            spinner.stop(f"  {RED}✗{RESET} {domain} ({tool_elapsed:.0f}s)")
            results[domain] = {"status": "error", "result": str(e)}
            errors[domain] = str(e)

    elapsed = time.monotonic() - start
    logger.info("All MCP tools completed in %.1fs", elapsed)

    ok_count = sum(1 for r in results.values() if r["status"] == "success")
    error_count = len(results) - ok_count
    print(f"\n  Done ({elapsed:.0f}s)\n", flush=True)

    output_parts = [f"# EKS Review Results for cluster: {cluster_name}\n"]
    for domain in domains:
        if domain in results:
            r = results[domain]
            icon = "PASS" if r["status"] == "success" else "FAIL"
            output_parts.append(f"\n## {domain.upper()} [{icon}]\n{r['result']}\n")

    output_parts.append(f"\n---\nReview completed in {elapsed:.1f}s across {len(results)} domains.")

    return {
        "text": "\n".join(output_parts),
        "ok_count": ok_count,
        "error_count": error_count,
        "errors": errors,
        "total": len(results),
    }
