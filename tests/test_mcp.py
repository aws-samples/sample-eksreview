"""Tests for the MCP client setup — env filter logic."""

from __future__ import annotations

import os

import pytest

from eks_review_agent.orchestration.mcp import _filter_env_for_mcp


class TestFilterEnv:
    def _set_env(self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> None:
        # Clear, then set only the test scenario's env
        for key in list(os.environ.keys()):
            monkeypatch.delenv(key, raising=False)
        for k, v in env.items():
            monkeypatch.setenv(k, v)

    def test_aws_credentials_pass_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "AWS_ACCESS_KEY_ID": "AKIA",
            "AWS_SECRET_ACCESS_KEY": "secret",
            "AWS_SESSION_TOKEN": "token",
            "AWS_REGION": "us-west-2",
        })
        env = _filter_env_for_mcp()
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA"
        assert env["AWS_SECRET_ACCESS_KEY"] == "secret"
        assert env["AWS_SESSION_TOKEN"] == "token"
        assert env["AWS_REGION"] == "us-west-2"

    def test_aws_profile_credentials_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_env(monkeypatch, {
            "AWS_PROFILE": "dev",
            "AWS_CONFIG_FILE": "/home/me/.aws/config",
            "AWS_SHARED_CREDENTIALS_FILE": "/home/me/.aws/credentials",
            "HOME": "/home/me",
            "PATH": "/usr/bin",
        })
        env = _filter_env_for_mcp()
        assert env["AWS_PROFILE"] == "dev"
        assert env["AWS_CONFIG_FILE"] == "/home/me/.aws/config"
        assert env["HOME"] == "/home/me"

    def test_aws_irsa_web_identity_pass_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_env(monkeypatch, {
            "AWS_ROLE_ARN": "arn:aws:iam::123:role/foo",
            "AWS_WEB_IDENTITY_TOKEN_FILE": "/tmp/token",
            "AWS_CONTAINER_CREDENTIALS_RELATIVE_URI": "/v2/credentials/abc",
        })
        env = _filter_env_for_mcp()
        assert env["AWS_ROLE_ARN"] == "arn:aws:iam::123:role/foo"
        assert env["AWS_WEB_IDENTITY_TOKEN_FILE"] == "/tmp/token"
        assert env["AWS_CONTAINER_CREDENTIALS_RELATIVE_URI"] == "/v2/credentials/abc"

    def test_bedrock_creds_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "BEDROCK_AWS_ACCESS_KEY_ID": "AKIA-bedrock",
            "BEDROCK_AWS_SECRET_ACCESS_KEY": "bedrock-secret",
            "BEDROCK_AWS_SESSION_TOKEN": "bedrock-token",
            "BEDROCK_AWS_REGION": "us-east-1",
            # control: a real AWS_ var should still pass
            "AWS_REGION": "us-west-2",
        })
        env = _filter_env_for_mcp()
        for key in (
            "BEDROCK_AWS_ACCESS_KEY_ID",
            "BEDROCK_AWS_SECRET_ACCESS_KEY",
            "BEDROCK_AWS_SESSION_TOKEN",
            "BEDROCK_AWS_REGION",
        ):
            assert key not in env, f"{key} should be filtered out"
        assert env["AWS_REGION"] == "us-west-2"

    def test_bedrock_api_key_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AWS_BEARER_TOKEN_BEDROCK matches the AWS_ allow-prefix, so it must be
        # denied explicitly or it would leak to the cluster-facing subprocess.
        self._set_env(monkeypatch, {
            "AWS_BEARER_TOKEN_BEDROCK": "bedrock-api-key-secret",
            # control: a real AWS_ var should still pass
            "AWS_REGION": "us-west-2",
        })
        env = _filter_env_for_mcp()
        assert "AWS_BEARER_TOKEN_BEDROCK" not in env, (
            "Bedrock API key must not reach the MCP subprocess"
        )
        assert env["AWS_REGION"] == "us-west-2"

    def test_agent_only_config_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "MODEL_ID": "us.anthropic.claude-opus-4-6-v1",
            "MODEL_TEMPERATURE": "0.5",
            "MODEL_MAX_TOKENS": "8000",
            "LOG_LEVEL": "DEBUG",
            "EKS_MCP_SERVER_DIR": "/path/to/mcp",
            "EKS_REVIEW_OFFLINE": "1",
            "REPORTS_DIR": "reports",
            "KNOWLEDGE_DIR": ".knowledge",
            "KNOWLEDGE_CHUNK_SIZE": "2048",
            "CONVERSATION_SUMMARY_RATIO": "0.4",
            "CONVERSATION_PRESERVE_MESSAGES": "10",
        })
        env = _filter_env_for_mcp()
        for key in (
            "MODEL_ID", "MODEL_TEMPERATURE", "MODEL_MAX_TOKENS",
            "LOG_LEVEL", "EKS_MCP_SERVER_DIR", "EKS_REVIEW_OFFLINE",
            "REPORTS_DIR", "KNOWLEDGE_DIR", "KNOWLEDGE_CHUNK_SIZE",
            "CONVERSATION_SUMMARY_RATIO", "CONVERSATION_PRESERVE_MESSAGES",
        ):
            assert key not in env, f"{key} should be filtered out"

    def test_third_party_tokens_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "GITHUB_TOKEN": "ghp_xxx",
            "OPENAI_API_KEY": "sk-xxx",
            "DATABASE_URL": "postgres://...",
            "SLACK_BOT_TOKEN": "xoxb-...",
            "ANTHROPIC_API_KEY": "anthropic-xxx",
        })
        env = _filter_env_for_mcp()
        for key in (
            "GITHUB_TOKEN", "OPENAI_API_KEY", "DATABASE_URL",
            "SLACK_BOT_TOKEN", "ANTHROPIC_API_KEY",
        ):
            assert key not in env, f"{key} should be filtered out"

    def test_proxy_vars_pass_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "HTTPS_PROXY": "http://corp:3128",
            "HTTP_PROXY": "http://corp:3128",
            "NO_PROXY": "169.254.169.254,localhost",
            "https_proxy": "http://corp:3128",
        })
        env = _filter_env_for_mcp()
        assert env["HTTPS_PROXY"] == "http://corp:3128"
        assert env["NO_PROXY"] == "169.254.169.254,localhost"
        assert env["https_proxy"] == "http://corp:3128"

    def test_ca_bundle_vars_pass_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._set_env(monkeypatch, {
            "AWS_CA_BUNDLE": "/etc/ssl/corp.pem",
            "REQUESTS_CA_BUNDLE": "/etc/ssl/corp.pem",
            "SSL_CERT_FILE": "/etc/ssl/corp.pem",
            "SSL_CERT_DIR": "/etc/ssl/certs",
            "CURL_CA_BUNDLE": "/etc/ssl/corp.pem",
        })
        env = _filter_env_for_mcp()
        for key in (
            "AWS_CA_BUNDLE", "REQUESTS_CA_BUNDLE", "SSL_CERT_FILE",
            "SSL_CERT_DIR", "CURL_CA_BUNDLE",
        ):
            assert key in env, f"{key} should pass through"

    def test_fastmcp_log_level_always_added(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        self._set_env(monkeypatch, {})
        env = _filter_env_for_mcp()
        assert env["FASTMCP_LOG_LEVEL"] == "ERROR"
