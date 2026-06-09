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

"""Shared utility functions for EKS review check handlers."""

from typing import Any, Dict, List


def _aggregate_by_owner(pods: List[Any], non_compliant_pod_names: List[str]) -> List[str]:
    """Aggregate pod names to their owner workloads (Deployment/StatefulSet/DaemonSet/ReplicaSet).

    For each non-compliant pod, resolve its owner reference and deduplicate so the
    impacted_resources list contains owner workload names instead of individual pod names.
    Pods without a recognised owner are kept as-is.

    Args:
        pods: List of pod objects (K8s API resources).
        non_compliant_pod_names: List of "namespace/pod-name" strings that failed the check.

    Returns:
        Deduplicated list of owner workload identifiers (e.g. "ns/Deployment/my-app").
    """
    # Build a lookup: "namespace/pod-name" -> pod object
    pod_lookup: Dict[str, Any] = {}
    for pod in pods:
        try:
            pod_dict = pod.to_dict() if hasattr(pod, 'to_dict') else pod
            metadata = pod_dict.get('metadata', {})
            ns = metadata.get('namespace', 'default')
            name = metadata.get('name', '')
            pod_lookup[f'{ns}/{name}'] = pod_dict
        except Exception:
            continue

    owner_set: set = set()
    owner_kind_priority = {'Deployment', 'StatefulSet', 'DaemonSet', 'ReplicaSet', 'Job', 'CronJob'}

    for pod_key in non_compliant_pod_names:
        pod_dict = pod_lookup.get(pod_key)
        if not pod_dict:
            # Pod not found in lookup – keep the raw name
            owner_set.add(pod_key)
            continue

        metadata = pod_dict.get('metadata', {})
        owner_refs = metadata.get('ownerReferences', [])
        ns = metadata.get('namespace', 'default')

        resolved = False
        for ref in owner_refs:
            kind = ref.get('kind', '')
            owner_name = ref.get('name', '')
            if kind in owner_kind_priority and owner_name:
                owner_set.add(f'{ns}/{kind}/{owner_name}')
                resolved = True
                break

        if not resolved:
            # No recognised owner – keep the pod name
            owner_set.add(pod_key)

    return sorted(owner_set)


def compact_response(summary: str, check_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Transform check results into a compact JSON structure for LLM consumption.

    Applies three optimizations without truncating or losing data:
    1. Short key names (check_name→n, severity→s, details→d, impacted_resources→r)
    2. Separate passing checks (name-only list) from failing checks (full detail)
    3. Group impacted_resources by namespace prefix to eliminate repetition

    Args:
        summary: The summary string for the response.
        check_results: List of check result dicts from handler execution.

    Returns:
        Compact dict ready for json.dumps().
    """
    passed = []
    failed = []

    for result in check_results:
        if result.get('compliant', False):
            passed.append(result.get('check_name', ''))
        else:
            resources = result.get('impacted_resources', [])
            grouped = _group_resources_by_namespace(resources)

            failed.append({
                'n': result.get('check_name', ''),
                's': _short_severity(result.get('severity', 'medium')),
                'd': result.get('details', ''),
                'r': grouped,
            })

    return {
        'summary': summary,
        'passed': passed,
        'failed': failed,
    }


def compact_upgrade_response(summary: str, check_results: List[Dict[str, Any]],
                              current_version: str, target_version: str,
                              blockers: int, warnings: int) -> Dict[str, Any]:
    """Transform upgrade check results into a compact JSON structure for LLM consumption.

    Same compaction as compact_response but adds upgrade-specific fields:
    - 't' for upgrade_timing (b=before, a=after)
    - Top-level version and verdict metadata

    Args:
        summary: The summary string.
        check_results: List of upgrade check result dicts.
        current_version: Current K8s version.
        target_version: Target K8s version.
        blockers: Count of critical blockers.
        warnings: Count of warnings.

    Returns:
        Compact dict ready for json.dumps().
    """
    passed = []
    failed = []

    for result in check_results:
        if result.get('compliant', False):
            passed.append(result.get('check_id', '') + ':' + result.get('check_name', ''))
        else:
            resources = result.get('impacted_resources', [])
            grouped = _group_resources_by_namespace(resources)

            entry = {
                'id': result.get('check_id', ''),
                'n': result.get('check_name', ''),
                's': _short_severity(result.get('severity', 'medium')),
                'd': result.get('details', ''),
                'r': grouped,
            }
            timing = result.get('upgrade_timing', '')
            if timing:
                entry['t'] = 'b' if timing == 'before' else 'a'
            failed.append(entry)

    return {
        'v': f'{current_version}->{target_version}',
        'blockers': blockers,
        'warnings': warnings,
        'summary': summary,
        'passed': passed,
        'failed': failed,
    }


def _short_severity(severity: str) -> str:
    """Convert severity to single-char abbreviation."""
    s = severity.lower() if severity else 'M'
    if s in ('critical', 'Critical'):
        return 'C'
    elif s in ('high', 'High'):
        return 'H'
    elif s in ('medium', 'Medium'):
        return 'M'
    elif s in ('low', 'Low'):
        return 'L'
    return s[0].upper() if s else 'M'


def _group_resources_by_namespace(resources: List[str], max_per_ns: int = 5) -> Any:
    """Group resource strings by namespace prefix and cap long lists.

    If resources follow "namespace/name" or "Type: namespace/name" patterns,
    groups them by namespace to eliminate prefix repetition.
    If fewer than 4 resources or no namespace pattern detected, returns as-is.

    When a namespace has more than max_per_ns resources, keeps the first 3
    and appends a count summary (e.g. "... and 38 more"). This prevents
    output explosion when clusters have many similar workloads (e.g. scale-test
    with 41 deployments appearing in every check).

    Returns:
        Either the original list (if small/ungroupable) or a dict of {namespace: [names]}.
    """
    if len(resources) < 4:
        return resources

    grouped: Dict[str, List[str]] = {}
    ungrouped: List[str] = []

    for res in resources:
        # Handle "Type: namespace/name" format
        parts = res.split(': ', 1)
        if len(parts) == 2:
            prefix = parts[0]  # e.g. "Deployment"
            path = parts[1]    # e.g. "scale-test/scale-app-1"
        else:
            prefix = ''
            path = res

        # Split on namespace separator
        ns_parts = path.split('/', 1)
        if len(ns_parts) == 2:
            ns = ns_parts[0]
            name = ns_parts[1]
            if prefix:
                name = f'{prefix}: {name}'
            if ns not in grouped:
                grouped[ns] = []
            grouped[ns].append(name)
        else:
            ungrouped.append(res)

    # Only use grouping if it actually reduces size
    if not grouped or len(grouped) >= len(resources):
        return resources

    # Cap long namespace lists to reduce output token bloat
    for ns, items in grouped.items():
        if len(items) > max_per_ns:
            total = len(items)
            grouped[ns] = items[:3] + [f'... and {total - 3} more']

    result = grouped
    if ungrouped:
        result['_other'] = ungrouped
    return result
