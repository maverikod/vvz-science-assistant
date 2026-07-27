"""Client configuration."""
from __future__ import annotations
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class ProxyConfig:
    protocol: str = "https"
    host: str = "mcp-proxy.techsup.od.ua"
    port: int = 3004
    token_header: str | None = None
    token: str | None = None
    cert: str | None = None
    key: str | None = None
    ca: str | None = None
    check_hostname: bool = False
    timeout: float = 120.0
    server_id: str = "science-assistant-vvz"
    copy_number: int = 1
    verify_version: bool = True
    retries: int = 3
    retry_delay: float = 1.0

    @classmethod
    def from_env(cls) -> "ProxyConfig":
        def opt(name: str) -> str | None:
            value = os.getenv(name)
            return value or None
        return cls(
            protocol=os.getenv("SCIENCE_ASSISTANT_PROXY_PROTOCOL", "https"),
            host=os.getenv("SCIENCE_ASSISTANT_PROXY_HOST", "mcp-proxy.techsup.od.ua"),
            port=int(os.getenv("SCIENCE_ASSISTANT_PROXY_PORT", "3004")),
            token_header=opt("SCIENCE_ASSISTANT_PROXY_TOKEN_HEADER"),
            token=opt("SCIENCE_ASSISTANT_PROXY_TOKEN"),
            cert=opt("SCIENCE_ASSISTANT_PROXY_CERT"),
            key=opt("SCIENCE_ASSISTANT_PROXY_KEY"),
            ca=opt("SCIENCE_ASSISTANT_PROXY_CA"),
            check_hostname=os.getenv("SCIENCE_ASSISTANT_PROXY_CHECK_HOSTNAME", "false").lower() in {"1","true","yes"},
            timeout=float(os.getenv("SCIENCE_ASSISTANT_PROXY_TIMEOUT", "120")),
            server_id=os.getenv("SCIENCE_ASSISTANT_SERVER_ID", "science-assistant-vvz"),
            copy_number=int(os.getenv("SCIENCE_ASSISTANT_COPY_NUMBER", "1")),
            verify_version=os.getenv("SCIENCE_ASSISTANT_VERIFY_VERSION", "true").lower() not in {"0","false","no"},
            retries=int(os.getenv("SCIENCE_ASSISTANT_RETRIES", "3")),
            retry_delay=float(os.getenv("SCIENCE_ASSISTANT_RETRY_DELAY", "1")),
        )
