'''A practice OPC UA server laid out like DeltaV module paths.

    python -m mfgkit.opcua.fake_server # opc.tcp://localhost:4840
'''

from __future__ import annotations

import argparse
import asyncio
import logging
import math
import random

from asyncua import Server, ua

NAMESPACE = "urn:acp:fake-deltav"

# path -> starting value
TAGS = {
    "TIT101/AI1/OUT.CV": 85.0,
    "FIC2101/PID1/PV.CV": 120.0,
    "FIC2101/PID1/SP.CV": 120.0,
    "FIC2101/PID1/OUT.CV": 45.0,
    "FIC2101/PID1/MODE.ACTUAL": "AUTO",
    "XV3001/DC1/PV_D.CV": 1,
}


async def build(port: int = 4840):
    """A started server and {path: variable node}; call `await server.stop()` when done."""
    server = Server()
    await server.init()
    server.set_endpoint(f"opc.tcp://0.0.0.0:{port}/")
    server.set_server_name("Fake DeltaV")
    ns = await server.register_namespace(NAMESPACE)
    modules = await server.nodes.objects.add_folder(ua.NodeId("Modules", ns), f"{ns}:Modules")

    folders, variables = {}, {}
    for path, start in TAGS.items():
        *parents, leaf = path.split("/")
        parent = modules
        for i in range(len(parents)):
            key = "/".join(parents[:i + 1])
            if key not in folders:
                folders[key] = await parent.add_object(ua.NodeId(key, ns), f"{ns}:{parents[i]}")
            parent = folders[key]
        var = await parent.add_variable(ua.NodeId(path, ns), f"{ns}:{leaf}", start)
        await var.set_writable()
        variables[path] = var
    await server.start()
    return server, variables


async def simulate(variables, period: float = 1.0) -> None:
    """Move the values the way a running loop would, forever."""
    t = 0
    while True:
        t += period
        sp = 120.0 if (t // 60) % 2 == 0 else 130.0          # setpoint step every minute
        pv = await variables["FIC2101/PID1/PV.CV"].read_value()
        pv += 0.2 * (sp - pv) + random.gauss(0, 0.3)
        await variables["FIC2101/PID1/SP.CV"].write_value(sp)
        await variables["FIC2101/PID1/PV.CV"].write_value(round(pv, 2))
        await variables["FIC2101/PID1/OUT.CV"].write_value(round(45 + 2 * (sp - pv), 2))
        await variables["TIT101/AI1/OUT.CV"].write_value(round(85 + 3 * math.sin(t / 120), 2))
        if random.random() < 0.02:
            mode = await variables["FIC2101/PID1/MODE.ACTUAL"].read_value()
            await variables["FIC2101/PID1/MODE.ACTUAL"].write_value("MAN" if mode == "AUTO" else "AUTO")
        if random.random() < 0.05:
            valve = await variables["XV3001/DC1/PV_D.CV"].read_value()
            await variables["XV3001/DC1/PV_D.CV"].write_value(1 - valve)
        await asyncio.sleep(period)


async def main(port: int) -> None:
    server, variables = await build(port)
    print(f"Fake DeltaV on opc.tcp://localhost:{port}/  namespace {NAMESPACE}  (Ctrl-C to stop)")
    try:
        await simulate(variables)
    finally:
        await server.stop()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Practice OPC UA server")
    parser.add_argument("--port", type=int, default=4840)
    logging.basicConfig(level=logging.WARNING)
    try:
        asyncio.run(main(parser.parse_args().port))
    except KeyboardInterrupt:
        pass