import asyncio
import socket

import pytest

pytest.importorskip('asyncua')

from mfgkit.opcua.client import ServerConfig, browse, connect
from mfgkit.opcua.fake_server import NAMESPACE, build
from mfgkit.opcua.values import quality, split_value


def free_port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def run_with_server(test):
    '''Start the practice server, run `test(cfg, variables)`,
    then stop the server'''
    async def main():
        port = free_port()
        server, variables = await build(port)
        try:
            cfg = ServerConfig(f'opc.tcp://127.0.0.1:{port}/')
            return await test(cfg, variables)
        finally:
            await server.stop()
        return asyncio.run(main())

def test_quality_and_split():
    assert quality(0) == 'good'
    assert quality(0x40000000) == 'uncertain'
    assert quality(0x80340000) == 'bad'
    assert split_value(True) == (1.0, None)
    assert split_value("AUTO") == (None, "AUTO")

def test_browse():
    async def test(cfg, _):
        async with connect(cfg) as client:
            assert "Modules" in [c['name'] for c in await browse(client)]
    run_with_server(test)