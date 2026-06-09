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

"""AWS helper for the EKS Review MCP Server."""

import boto3
import os
from awslabs.eks_review_mcp_server import __version__
from botocore.config import Config
from loguru import logger
from typing import Any, Dict, Optional


class AwsHelper:
    """Helper class for AWS operations.

    This class provides utility methods for interacting with AWS services,
    including region and profile management and client creation.

    This class implements a singleton pattern with a client cache to avoid
    creating multiple clients for the same service.
    """

    # Singleton instance
    _instance = None

    # Client cache keyed by (service_name, region, profile) to prevent
    # cross-region or cross-profile data leakage when the same service
    # is requested with different parameters in a single process.
    _client_cache: Dict[tuple, Any] = {}

    @staticmethod
    def get_aws_region() -> Optional[str]:
        """Get the AWS region from the environment if set."""
        return os.environ.get('AWS_REGION')

    @staticmethod
    def get_aws_profile() -> Optional[str]:
        """Get the AWS profile from the environment if set."""
        return os.environ.get('AWS_PROFILE')

    @classmethod
    def create_boto3_client(cls, service_name: str, region_name: Optional[str] = None) -> Any:
        """Create or retrieve a cached boto3 client with the appropriate profile and region.

        The client is configured with a custom user agent suffix 'awslabs/mcp/eks-review-mcp-server/{version}'
        to identify API calls made by the EKS Review MCP Server. Clients are cached to improve performance
        and reduce resource usage.

        Args:
            service_name: The AWS service name (e.g., 'ec2', 's3', 'eks')
            region_name: Optional region name override

        Returns:
            A boto3 client for the specified service

        Raises:
            Exception: If there's an error creating the client
        """
        try:
            # Get region from parameter or environment if set
            region: Optional[str] = (
                region_name if region_name is not None else cls.get_aws_region()
            )

            # Get profile from environment if set
            profile = cls.get_aws_profile()

            # Cache key includes region and profile so that calls with
            # different region/profile combinations don't collide on
            # service_name alone (which previously caused the first
            # client to be returned regardless of the requested region).
            cache_key = (service_name, region or 'default', profile or 'default')

            # Check if client is already in cache
            if cache_key in cls._client_cache:
                logger.info(
                    f'Using cached boto3 client for {service_name} '
                    f'(region={region or "default"}, profile={profile or "default"})'
                )
                return cls._client_cache[cache_key]

            # Create config with user agent suffix
            config = Config(user_agent_extra=f'awslabs/mcp/eks-review-mcp-server/{__version__}')

            # Create session with profile if specified
            if profile:
                session = boto3.Session(profile_name=profile)
                if region is not None:
                    client = session.client(service_name, region_name=region, config=config)
                else:
                    client = session.client(service_name, config=config)
            else:
                if region is not None:
                    client = boto3.client(service_name, region_name=region, config=config)
                else:
                    client = boto3.client(service_name, config=config)

            # Cache the client
            cls._client_cache[cache_key] = client

            return client
        except Exception as e:
            # Re-raise with more context
            raise Exception(f'Failed to create boto3 client for {service_name}: {str(e)}')