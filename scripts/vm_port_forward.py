#!/usr/bin/env python3
"""Forward localhost:8500 on the Mac to the MT5 bridge running in the Windows VM.

The bridge listens inside the VM (default 192.168.64.2:8500). UTM's own port
forwarding only takes effect on VM restart, and some clients refuse to load
pages from a private VM address, so this proxy gives the whole system one
stable local endpoint:

    Mac client -> localhost:8500 -> VM:8500 -> MT5 -> broker

It is a plain TCP relay, so REST and the /ws/prices WebSocket both pass through
untouched.

Usage:
    python3 scripts/vm_port_forward.py                 # defaults
    python3 scripts/vm_port_forward.py --vm-host 192.168.64.3 --listen-port 8600
"""

__all__ = ["main"]

import argparse
import asyncio
import logging

log = logging.getLogger("vm_port_forward")

DEFAULT_VM_HOST = "192.168.64.2"
DEFAULT_PORT = 8500


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy bytes one way until the source closes."""
    try:
        while True:
            chunk = await reader.read(65536)
            if not chunk:
                break
            writer.write(chunk)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        writer.close()


async def _handle(
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
    vm_host: str,
    vm_port: int,
) -> None:
    """Bridge one client connection to the VM, both directions at once."""
    try:
        vm_reader, vm_writer = await asyncio.open_connection(vm_host, vm_port)
    except OSError as e:
        log.error("vm_unreachable host=%s port=%s error=%s", vm_host, vm_port, e)
        client_writer.close()
        return

    await asyncio.gather(
        _pump(client_reader, vm_writer),
        _pump(vm_reader, client_writer),
        return_exceptions=True,
    )


async def _serve(listen_port: int, vm_host: str, vm_port: int) -> None:
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, vm_host, vm_port),
        host="127.0.0.1",
        port=listen_port,
    )
    log.info(
        "forwarding localhost:%s -> %s:%s (Ctrl+C to stop)",
        listen_port, vm_host, vm_port,
    )
    async with server:
        await server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vm-host", default=DEFAULT_VM_HOST, help="VM address running mt5_api_server.py")
    parser.add_argument("--vm-port", type=int, default=DEFAULT_PORT, help="Port the bridge listens on inside the VM")
    parser.add_argument("--listen-port", type=int, default=DEFAULT_PORT, help="Local port to expose on the Mac")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    try:
        asyncio.run(_serve(args.listen_port, args.vm_host, args.vm_port))
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
