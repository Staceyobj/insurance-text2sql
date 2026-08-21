"""Offline tests for the IPv4-first getaddrinfo reorder (DEPLOYMENT.md §1).

The Azure runtime has no IPv6 egress while the Zhipu endpoint is dual-stack
with AAAA first; without the reorder the sequential connect exhausts the
client timeout on the dead v6 address. These tests stub the original
getaddrinfo — no network, no API key.
"""

from __future__ import annotations

import socket

import pytest

from text2sql import llm
from text2sql.config import Settings


def _ai(family: int, host: str) -> tuple:
    # getaddrinfo result entry: (family, type, proto, canonname, sockaddr)
    addr = ("::1", 0, 0, 0) if family == socket.AF_INET6 else ("127.0.0.1", 0)
    return (family, socket.SOCK_STREAM, 6, "", addr)


@pytest.fixture()
def stub_getaddrinfo(monkeypatch):
    def install(results):
        monkeypatch.setattr(llm, "_orig_getaddrinfo", lambda *a, **k: list(results))
    return install


def test_ipv4_moved_before_ipv6(stub_getaddrinfo):
    stub_getaddrinfo([_ai(socket.AF_INET6, "v6"), _ai(socket.AF_INET, "v4")])
    out = llm._ipv4_first("host", 443)
    assert out[0][0] == socket.AF_INET
    assert out[1][0] == socket.AF_INET6
    assert len(out) == 2  # nothing dropped, order only


def test_ipv6_only_left_untouched(stub_getaddrinfo):
    only_v6 = [_ai(socket.AF_INET6, "v6")]
    stub_getaddrinfo(only_v6)
    assert llm._ipv4_first("host", 443) == only_v6


def test_build_llm_installs_the_reorder(monkeypatch):
    # Deliberately NOT restored after the test: build_llm installs the reorder
    # process-wide for good in production, and undoing it here would only make
    # the test less faithful to real behavior.
    llm.build_llm(Settings(zhipuai_api_key="test-key"))
    assert socket.getaddrinfo is llm._ipv4_first
