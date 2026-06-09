# Amazon EKS Review MCP Server

The Amazon EKS Review MCP Server provides AI code assistants with comprehensive assessment tools to evaluate Amazon EKS clusters against AWS best practices. This server enables large language models (LLMs) to perform automated reviews across five critical domains: networking, security, resiliency, Karpenter configuration, and Cluster Autoscaler setup. By integrating this server into AI code assistants, development teams can proactively identify configuration issues, security vulnerabilities, and optimization opportunities in their EKS infrastructure.

The EKS Review MCP Server streamlines the cluster assessment process by automatically analyzing cluster configurations, identifying non-compliant resources, and providing detailed findings that AI assistants can use to generate context-aware remediation guidance. This approach transforms complex EKS best practices documentation into actionable insights, helping teams maintain secure, resilient, and well-architected Kubernetes environments.

## Key Features

* Performs comprehensive networking assessments including cluster endpoint access control, multi-AZ node distribution, VPC configuration, subnet capacity validation, and security group settings.
* Evaluates security posture across IAM/RBAC configurations, pod security standards, encryption settings, secrets management, and infrastructure security controls.
* Assesses resiliency through 28 checks covering application workloads (pod controllers, replicas, health probes, PDBs), control plane configuration, and data plane infrastructure including autoscaling (HPA/VPA/CA) and monitoring.
* Reviews Karpenter best practices including deployment configuration, NodePool settings, instance selection strategies, and spot instance optimization.
* Analyzes Cluster Autoscaler configuration including deployment health, version compatibility, auto-discovery settings, and node group configurations.
* Automatically detects EKS Auto Mode clusters and skips Karpenter/Cluster Autoscaler checks when not applicable.
* Optimizes performance by fetching Kubernetes resources once and sharing them across all checks, minimizing API calls.
* Returns structured check results with compliance status, impacted resources, configuration details, and severity levels for AI-driven remediation.

## Prerequisites

* [Install Python 3.10+](https://www.python.org/downloads/release/python-3100/)
* [Install the `uv` package manager](https://docs.astral.sh/uv/getting-started/installation/)
* [Install and configure the AWS CLI with credentials](https://docs.aws.amazon.com/cli/latest/userguide/cli-chap-configure.html)
* [Install and configure kubectl](https://kubernetes.io/docs/tasks/tools/)

## Setup

Add these IAM policies to the IAM role or user that you use to review your EKS clusters.

### Required IAM Permissions

The EKS Review MCP Server requires read-only access to EKS clusters and related AWS resources:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "eks:DescribeCluster",
        "eks:ListClusters",
        "eks:DescribeNodegroup",
        "eks:ListNodegroups",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeSecurityGroups",
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListRolePolicies",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion"
      ],
      "Resource": "*"
    }
  ]
}
```

### Kubernetes API Access Requirements

All Kubernetes API operations will only work when one of the following conditions is met:

1. The user's principal (IAM role/user) actually created the EKS cluster being accessed
2. An EKS Access Entry has been configured for the user's principal

If you encounter authorization errors when using Kubernetes API operations, verify that an access entry has been properly configured for your principal.

## Quickstart

This quickstart guide walks you through the steps to configure the Amazon EKS Review MCP Server for use with both the [Cursor](https://www.cursor.com/en/downloads) IDE and the [Amazon Q Developer CLI](https://github.com/aws/amazon-q-developer-cli). By following these steps, you'll set up your development environment to leverage the EKS Review MCP Server's assessment tools for evaluating your Amazon EKS clusters.

**Set up Cursor**

| Cursor | VS Code |
|:------:|:-------:|
| [![Install MCP Server](https://cursor.com/deeplink/mcp-install-light.svg)](https://cursor.com/en/install-mcp?name=awslabs.eks-review-mcp-server&config=eyJhdXRvQXBwcm92ZSI6W10sImRpc2FibGVkIjpmYWxzZSwiY29tbWFuZCI6InV2eCBhd3NsYWJzLmVrcy1yZXZpZXctbWNwLXNlcnZlckBsYXRlc3QiLCJlbnYiOnsiRkFTVE1DUF9MT0dfTEVWRUwiOiJFUlJPUiJ9LCJ0cmFuc3BvcnRUeXBlIjoic3RkaW8ifQ%3D%3D) | [![Install on VS Code](https://img.shields.io/badge/Install_on-VS_Code-FF9900?style=flat-square&logo=visualstudiocode&logoColor=white)](https://insiders.vscode.dev/redirect/mcp/install?name=EKS%20Review%20MCP%20Server&config=%7B%22autoApprove%22%3A%5B%5D%2C%22disabled%22%3Afalse%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22awslabs.eks-review-mcp-server%40latest%22%5D%2C%22env%22%3A%7B%22FASTMCP_LOG_LEVEL%22%3A%22ERROR%22%7D%2C%22transportType%22%3A%22stdio%22%7D) |

**Set up the Amazon Q Developer CLI**

1. Install the [Amazon Q Developer CLI](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-installing.html).
2. The Q Developer CLI supports MCP servers for tools and prompts out-of-the-box. Edit your Q Developer CLI's MCP configuration file named mcp.json following [these instructions](https://docs.aws.amazon.com/amazonq/latest/qdeveloper-ug/command-line-mcp-understanding-config.html).

   **For Mac/Linux:**

   ```json
   {
     "mcpServers": {
       "awslabs.eks-review-mcp-server": {
         "command": "uvx",
         "args": [
           "awslabs.eks-review-mcp-server@latest"
         ],
         "env": {
           "FASTMCP_LOG_LEVEL": "ERROR"
         },
         "autoApprove": [],
         "disabled": false
       }
     }
   }
   ```

   **For Windows:**

   ```json
   {
     "mcpServers": {
       "awslabs.eks-review-mcp-server": {
         "command": "uvx",
         "args": [
           "--from",
           "awslabs.eks-review-mcp-server@latest",
           "awslabs.eks-review-mcp-server.exe"
         ],
         "env": {
           "FASTMCP_LOG_LEVEL": "ERROR"
         },
         "autoApprove": [],
         "disabled": false
       }
     }
   }
   ```

3. Verify your setup by running the `/tools` command in the Q Developer CLI to see the available EKS Review MCP tools.

Note that this is a basic quickstart. You can enable additional capabilities, such as [running MCP servers in containers](https://github.com/awslabs/mcp?tab=readme-ov-file#running-mcp-servers-in-containers) or combining more MCP servers like the [AWS Documentation MCP Server](https://awslabs.github.io/mcp/servers/aws-documentation-mcp-server/) into a single MCP server definition. To view an example, see the [Installation and Setup](https://github.com/awslabs/mcp?tab=readme-ov-file#installation-and-setup) guide in AWS MCP Servers on GitHub.

## Configurations

### Environment Variables

The `env` field in the MCP server definition allows you to configure environment variables that control the behavior of the EKS Review MCP Server. For example:

```json
{
  "mcpServers": {
    "awslabs.eks-review-mcp-server": {
      "env": {
        "FASTMCP_LOG_LEVEL": "ERROR",
        "AWS_PROFILE": "my-profile",
        "AWS_REGION": "us-west-2",
        "HTTP_PROXY": "http://proxy.example.com:8080",
        "HTTPS_PROXY": "https://proxy.example.com:8080"
      }
    }
  }
}
```

#### `FASTMCP_LOG_LEVEL` (optional)

Sets the logging level verbosity for the server.

* Valid values: "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"
* Default: "WARNING"
* Example: `"FASTMCP_LOG_LEVEL": "ERROR"`

#### `AWS_PROFILE` (optional)

Specifies the AWS profile to use for authentication.

* Default: None (If not set, uses default AWS credentials)
* Example: `"AWS_PROFILE": "my-profile"`

#### `AWS_REGION` (optional)

Specifies the AWS region where EKS clusters are located, which will be used for all AWS service operations.

* Default: None (If not set, uses default AWS region)
* Example: `"AWS_REGION": "us-west-2"`

#### `HTTP_PROXY` / `HTTPS_PROXY` (optional)

Configures proxy settings for HTTP and HTTPS connections. These environment variables are used when the EKS Review MCP Server needs to make outbound connections to the Kubernetes API server through a proxy or firewall.

* Default: None (Direct connections are used if not set)
* Example: `"HTTP_PROXY": "http://proxy.example.com:8080"`, `"HTTPS_PROXY": "https://proxy.example.com:8080"`
* Note: Both variables can be set to the same proxy server if it handles both HTTP and HTTPS traffic

## Tools

The following tools are provided by the EKS Review MCP Server for assessing Amazon EKS clusters against AWS best practices. Each tool performs a comprehensive review of a specific domain and returns structured findings that AI assistants can use to generate remediation guidance.

### `check_eks_networking`

Evaluates EKS cluster networking configuration against AWS best practices.

**Features:**

* Assesses cluster endpoint access control and CIDR restrictions
* Validates multi-AZ node distribution for high availability
* Reviews VPC configuration including CIDR blocks and route tables
* Checks subnet capacity and availability zone compatibility
* Evaluates security group configurations
* Identifies networking misconfigurations that could impact cluster connectivity

**Parameters:**

* `cluster_name` (required): Name of the EKS cluster to assess
* `region` (optional): AWS region where the cluster is located. If not provided, uses default region

**Returns:**

* `check_results`: List of networking check results with compliance status, impacted resources, details, and severity
* `overall_compliant`: Boolean indicating whether all networking checks passed
* `summary`: Summary of the networking assessment results

### `check_eks_security`

Performs comprehensive security assessment of EKS cluster configuration.

**Features:**

* **IAM & Access Control**: Cluster Access Manager, endpoint security, service account tokens, RBAC least privilege, EKS Pod Identity
* **Pod Security**: Pod Security Standards, security contexts, privileged containers, host namespaces
* **Multi-Tenancy**: Namespace isolation, network policies, resource quotas
* **Detective Controls**: Audit logging, runtime monitoring, security scanning
* **Network Security**: Network policies, service mesh security, ingress/egress controls
* **Data Encryption & Secrets**: Encryption at rest, secrets management, KMS integration
* **Runtime Security**: Container runtime security, admission controllers
* **Infrastructure Security**: Node security, AMI hardening, security groups

**Parameters:**

* `cluster_name` (required): Name of the EKS cluster to assess
* `namespace` (optional): Optional namespace to limit the security check scope

**Returns:**

* `check_results`: List of security check results with compliance status, impacted resources, details, and severity
* `overall_compliant`: Boolean indicating whether all security checks passed
* `summary`: Summary of the security assessment results

### `check_eks_resiliency`

Assesses EKS cluster resiliency through 26 comprehensive checks across application, control plane, and data plane.

**Features:**

* **Application Checks (A1-A14)**: Pod controllers, multiple replicas, pod anti-affinity, readiness/liveness probes, PodDisruptionBudgets, resource requests/limits, HPA/VPA configuration, monitoring, graceful termination, service mesh, centralized logging
* **Control Plane Checks (C1-C5)**: Control plane logging, cluster authentication, large cluster optimization, endpoint access control, admission webhook configuration
* **Data Plane Checks (D1-D7)**: Node autoscaling (CA/Karpenter), multi-AZ distribution, resource requests/limits, namespace quotas and limits, CoreDNS monitoring and configuration

**Parameters:**

* `cluster_name` (required): Name of the EKS cluster to assess
* `namespace` (optional): Optional namespace to limit the resiliency check scope

**Returns:**

* `check_results`: List of resiliency check results with compliance status, impacted resources, details, and severity
* `overall_compliant`: Boolean indicating whether all resiliency checks passed
* `summary`: Summary of the resiliency assessment results

### `check_karpenter_best_practices`

Reviews Karpenter configuration and deployment against best practices.

**Features:**

* Validates Karpenter deployment health and configuration
* Assesses NodePool configurations and instance selection strategies
* Reviews spot instance optimization settings
* Checks consolidation and disruption policies
* Evaluates resource limits and constraints
* Automatically skips for EKS Auto Mode clusters

**Parameters:**

* `cluster_name` (required): Name of the EKS cluster to assess

**Returns:**

* `check_results`: List of Karpenter check results with compliance status, impacted resources, details, and severity
* `overall_compliant`: Boolean indicating whether all Karpenter checks passed
* `summary`: Summary of the Karpenter assessment results

### `check_cluster_autoscaler_best_practices`

Evaluates Cluster Autoscaler configuration and deployment.

**Features:**

* Validates Cluster Autoscaler deployment health
* Checks version compatibility with EKS cluster version
* Reviews auto-discovery configuration
* Assesses node group settings and scaling policies
* Evaluates resource requests and limits
* Automatically skips for EKS Auto Mode clusters

**Parameters:**

* `cluster_name` (required): Name of the EKS cluster to assess

**Returns:**

* `check_results`: List of Cluster Autoscaler check results with compliance status, impacted resources, details, and severity
* `overall_compliant`: Boolean indicating whether all Cluster Autoscaler checks passed
* `summary`: Summary of the Cluster Autoscaler assessment results

## Check Result Structure

Each check returns a structured result with the following fields:

* **check_name**: Name of the best practice check
* **compliant**: Boolean indicating pass/fail status
* **severity**: Risk level (High, Medium, Low)
* **impacted_resources**: List of resources that failed the check
* **details**: Current configuration state and specific findings
* **remediation**: Empty string (AI assistants generate context-aware remediation based on details and impacted_resources)

## AI Assistant Integration

The EKS Review MCP Server is designed to work seamlessly with AI code assistants:

1. **Run Assessment**: AI assistant invokes one or more check tools
2. **Analyze Results**: AI reviews check results, focusing on non-compliant checks
3. **Generate Remediation**: AI creates context-aware remediation steps based on:
   * Impacted resources and their current state
   * Check details and severity
   * AWS and Kubernetes best practices
   * Cluster-specific context
4. **Prioritize Actions**: AI prioritizes remediation by severity (High → Medium → Low)
5. **Provide Guidance**: AI delivers actionable steps including commands, YAML manifests, and configuration changes

## Security & Permissions

### Features

The EKS Review MCP Server implements the following security features:

1. **Read-Only Operations**: All checks are read-only and do not modify cluster configuration
2. **AWS Authentication**: Uses AWS credentials from the environment for secure authentication
3. **Kubernetes Authentication**: Generates temporary credentials for Kubernetes API access
4. **SSL Verification**: Enforces SSL verification for all Kubernetes API calls
5. **Client Caching**: Caches Kubernetes clients with TTL-based expiration for security and performance
6. **Resource Sharing**: Fetches Kubernetes resources once and shares across checks to minimize API calls

### Considerations

When using the EKS Review MCP Server, consider the following:

* **AWS Credentials**: The server needs read-only permissions to EKS and related AWS resources
* **Kubernetes Access**: The server requires read access to Kubernetes API for resource inspection
* **Network Security**: Ensure network connectivity to EKS cluster endpoints
* **Authentication**: Use appropriate authentication mechanisms for Kubernetes API access
* **Authorization**: Configure RBAC with read-only permissions for cluster assessment

### `autoApprove` (optional)

An array within the MCP server definition that lists tool names to be automatically approved by the EKS Review MCP Server client, bypassing user confirmation for those specific tools. For example:

**For Mac/Linux:**
```json
{
  "mcpServers": {
    "awslabs.eks-review-mcp-server": {
      "command": "uvx",
      "args": [
        "awslabs.eks-review-mcp-server@latest"
      ],
      "env": {
        "AWS_PROFILE": "eks-review-profile",
        "AWS_REGION": "us-east-1",
        "FASTMCP_LOG_LEVEL": "INFO"
      },
      "autoApprove": [
        "check_eks_networking",
        "check_eks_security",
        "check_eks_resiliency",
        "check_karpenter_best_practices",
        "check_cluster_autoscaler_best_practices"
      ]
    }
  }
}
```

**For Windows:**
```json
{
  "mcpServers": {
    "awslabs.eks-review-mcp-server": {
      "command": "uvx",
      "args": [
        "--from",
        "awslabs.eks-review-mcp-server@latest",
        "awslabs.eks-review-mcp-server.exe"
      ],
      "env": {
        "AWS_PROFILE": "eks-review-profile",
        "AWS_REGION": "us-east-1",
        "FASTMCP_LOG_LEVEL": "INFO"
      },
      "autoApprove": [
        "check_eks_networking",
        "check_eks_security",
        "check_eks_resiliency",
        "check_karpenter_best_practices",
        "check_cluster_autoscaler_best_practices"
      ]
    }
  }
}
```

## Best Practices

* **Regular Assessments**: Run checks regularly to identify configuration drift and new issues
* **Comprehensive Reviews**: Run all five tools for a complete cluster assessment
* **Prioritize by Severity**: Address High severity findings first, then Medium and Low
* **Namespace Scoping**: Use namespace parameter to focus assessments on specific workloads
* **Combine with Monitoring**: Use assessment results alongside monitoring data for complete visibility
* **Document Findings**: Keep records of assessment results and remediation actions
* **Automate Reviews**: Integrate checks into CI/CD pipelines for continuous compliance

## Troubleshooting

* **Permission Errors**: Verify that your AWS credentials have the necessary read permissions for EKS and related services
* **Kubernetes API Errors**: Verify that the EKS cluster is running and accessible, and that you have proper access entry configured
* **Network Issues**: Check VPC and security group configurations allow connectivity to cluster endpoints
* **Client Errors**: Verify that the MCP client is configured correctly with proper environment variables
* **Log Level**: Increase the log level to DEBUG for more detailed logs: `"FASTMCP_LOG_LEVEL": "DEBUG"`

For general EKS issues, consult the [Amazon EKS Best Practices Guide](https://docs.aws.amazon.com/eks/latest/best-practices/).

## Related Resources

* [Amazon EKS Best Practices Guide](https://docs.aws.amazon.com/eks/latest/best-practices/)
* [Amazon EKS User Guide](https://docs.aws.amazon.com/eks/latest/userguide/)
* [Kubernetes Documentation](https://kubernetes.io/docs/)
* [Karpenter Documentation](https://karpenter.sh/)
* [Cluster Autoscaler Documentation](https://github.com/kubernetes/autoscaler/tree/master/cluster-autoscaler)
