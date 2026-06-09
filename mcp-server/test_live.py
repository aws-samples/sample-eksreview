#!/usr/bin/env python3
"""Direct live test of MCP handler checks against a real EKS cluster.

Usage:
    .venv/bin/python test_live.py <cluster-name> <region> [pillars...]

Examples:
    .venv/bin/python test_live.py eks-demo us-east-1                # all pillars
    .venv/bin/python test_live.py eks-demo us-east-1 security       # security only
    .venv/bin/python test_live.py eks-demo us-east-1 security networking  # two pillars
    .venv/bin/python test_live.py eks-demo us-east-1 observability  # observability only
    .venv/bin/python test_live.py eks-demo us-east-1 --json         # save raw JSON output

Valid pillars: security, resiliency, networking, karpenter, autoscaler, observability, upgrade

Note: Region is required for consistent behavior across all handlers.
"""

import asyncio
import json
import os
import sys
import time
from unittest.mock import MagicMock

sys.path.insert(0, '.')

from awslabs.eks_review_mcp_server.k8s_client_cache import K8sClientCache
from awslabs.eks_review_mcp_server.eks_security_handler import EKSSecurityHandler
from awslabs.eks_review_mcp_server.eks_resiliency_handler import EKSResiliencyHandler
from awslabs.eks_review_mcp_server.eks_networking_handler import EKSNetworkingHandler
from awslabs.eks_review_mcp_server.eks_karpenter_handler import EKSKarpenterHandler
from awslabs.eks_review_mcp_server.eks_cluster_autoscaler_handler import EKSClusterAutoscalerHandler
from awslabs.eks_review_mcp_server.eks_observability_handler import EKSObservabilityHandler
from awslabs.eks_review_mcp_server.eks_upgrade_handler import EKSUpgradeHandler

G="\033[32m"; R="\033[31m"; Y="\033[33m"; B="\033[36m"; D="\033[2m"; X="\033[0m"

ALL_PILLARS = ["security","resiliency","networking","karpenter","autoscaler","observability","upgrade"]

def pr(cid, r):
    ok=r.get('compliant',False); nm=r.get('check_name',cid)
    icon=f"{G}PASS{X}" if ok else f"{R}FAIL{X}"
    sev=r.get('severity','')
    print(f"  {icon}  [{sev}] {nm}")
    if not ok:
        res=r.get('impacted_resources',[])
        for x in res[:5]: print(f"         {D}{x}{X}")
        if len(res)>5: print(f"         {D}... and {len(res)-5} more{X}")
        det=str(r.get('details',''))
        if 'error' in det.lower(): print(f"         {R}{det[:200]}{X}")

def summary(name, results):
    p=sum(1 for r in results if r.get('compliant',False)); t=len(results); f=t-p
    pct=(p/t*100) if t>0 else 0; c=G if pct>=80 else(Y if pct>=50 else R)
    print(f"\n  {c}{name}: {p}/{t} passed ({pct:.0f}%){X}\n")
    return p,f

async def run_handler(name, handler_cls, method_name, cluster, client_cache, region, save_json):
    print(f"\n{B}{'='*60}{X}\n{B}  {name.upper()} CHECKS{X}\n{B}{'='*60}{X}\n")
    mock_mcp=MagicMock(); handler=handler_cls(mock_mcp, client_cache); ctx=MagicMock()
    method=getattr(handler, method_name)
    start=time.monotonic()
    # Different handlers have different signatures
    if name=="networking":
        resp=await method(ctx, cluster_name=cluster, region=region)
    elif name=="autoscaler":
        resp=await method(ctx, cluster_name=cluster, region=region, namespace='kube-system')
    elif name=="observability":
        resp=await method(ctx, cluster_name=cluster, region=region, lookback_days=7)
    elif name=="upgrade":
        resp=await method(ctx, cluster_name=cluster, region=region)
    elif name in ("security","resiliency"):
        resp=await method(ctx, cluster_name=cluster, namespace=None, region=region)
    elif name=="karpenter":
        resp=await method(ctx, cluster_name=cluster, namespace=None, region=region)
    else:
        resp=await method(ctx, cluster_name=cluster, namespace=None)
    elapsed=time.monotonic()-start
    for r in resp.check_results: pr(r.get('check_name','?'), r)
    p,f=summary(name.title(), resp.check_results)
    print(f"  {D}Completed in {elapsed:.1f}s{X}")
    if save_json:
        os.makedirs('test_output', exist_ok=True)
        # Extract JSON from content
        for c in resp.content:
            if hasattr(c,'text') and c.text:
                with open(f'test_output/{name}.json','w') as fh:
                    fh.write(c.text)
                print(f"  {D}JSON saved to test_output/{name}.json{X}")
                break
    return p,f,elapsed

HANDLER_MAP={
    "security":       ("security",       EKSSecurityHandler,           "check_eks_security"),
    "resiliency":     ("resiliency",     EKSResiliencyHandler,         "check_eks_resiliency"),
    "networking":     ("networking",     EKSNetworkingHandler,         "check_eks_networking"),
    "karpenter":      ("karpenter",      EKSKarpenterHandler,          "check_karpenter_best_practices"),
    "autoscaler":     ("autoscaler",     EKSClusterAutoscalerHandler,  "check_cluster_autoscaler_best_practices"),
    "observability":  ("observability",  EKSObservabilityHandler,      "check_eks_observability"),
    "upgrade":        ("upgrade",        EKSUpgradeHandler,            "check_eks_upgrade_readiness"),
}

async def main():
    args=[a for a in sys.argv[1:] if a!='--json']
    save_json='--json' in sys.argv
    if not args:
        print(__doc__); sys.exit(1)
    cluster=args[0]; region=None; pillars=[]
    for a in args[1:]:
        if a in ALL_PILLARS: pillars.append(a)
        elif not region and not a.startswith('-'): region=a
    if not pillars: pillars=ALL_PILLARS

    print(f"\n  EKS MCP Live Test")
    print(f"  Cluster: {cluster}")
    if region: print(f"  Region:  {region}")
    print(f"  Pillars: {', '.join(pillars)}")
    if save_json: print(f"  Output:  test_output/*.json")
    print()

    print(f"  Connecting...", end="", flush=True)
    cc=K8sClientCache()
    try: cc.get_client(cluster); print(f" {G}OK{X}")
    except Exception as e: print(f" {R}FAILED: {e}{X}"); sys.exit(1)

    tp=tf=0; tt=0.0
    for pname in pillars:
        if pname not in HANDLER_MAP:
            print(f"  {R}Unknown pillar: {pname}{X}"); continue
        nm,cls,meth=HANDLER_MAP[pname]
        try:
            p,f,t=await run_handler(nm,cls,meth,cluster,cc,region,save_json)
            tp+=p; tf+=f; tt+=t
        except Exception as e:
            print(f"\n  {R}ERROR in {nm}: {e}{X}")
            import traceback; traceback.print_exc(); tf+=1

    total=tp+tf; pct=(tp/total*100) if total>0 else 0
    c=G if pct>=80 else(Y if pct>=50 else R)
    print(f"\n{'='*60}")
    print(f"  OVERALL: {c}{tp}/{total} passed ({pct:.0f}%){X}")
    print(f"  Time:    {tt:.1f}s")
    print(f"{'='*60}\n")

if __name__=="__main__": asyncio.run(main())
