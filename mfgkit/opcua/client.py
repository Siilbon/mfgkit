'''Connect to an OPC UA server, read values, and look around its 
address space'''

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from asyncua import Client

from .values import Sample, to_sample

@dataclass
class ServerConfig:
    endpoint: str
    # asyncua serity string: "Policy,Mode,client, client key",
    # e.g. "Basic256Sha256,SignAndEncrypt,acp.der,acp.pem".
    # None = no security
    security: str | None = None
    username: str | None = None
    password: str | None = None
    timeout: float = 10.0

@asynccontextmanager
async def connect(cfg: ServerConfig):
    '''`async with connect(cfg) as client:` -- always disconnects, even on errors.'''
    client = Client(cfg.endpoint, timeout=cfg.timeout)
    if cfg.security:
        await client.set_security_string(cfg.security)
    if cfg.username:
        client.set_user(cfg.username)
        client.set_password(cfg.password or '')

    await client.connect()
    try:
        yield client
    finally:
        await client.disconnect()


async def read(client: Client, nodes: dict[str, str]) -> list[Sample]:
    '''read the current value of each {tag: node id}.'''
    samples = []
    for tag, node_id in nodes.items():
        dv = await client.get_node(node_id).read_data_value()
        samples.append(to_sample(tag=tag, 
                                 node_id=node_id, 
                                 dv=dv))

    return samples

async def browse(client: Client, node_id: str | None = None) -> list[dict]:
    '''One level of children under node_id (default: the Objects folder).'''
    start = client.get_node(nodeid=node_id) if node_id else client.nodes.objects
    children = []
    for child in await start.get_children():
        name = await child.read_browse_name()
        children.append({
            'name': name.Name,
            'class': (await child.read_node_class()).name,
            'node_id': child.nodeid.to_string(),
        })
    return children
