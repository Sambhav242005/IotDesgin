import socket

from zeroconf import IPVersion, ServiceInfo, Zeroconf

from .security import lan_addresses


class Advertisement:
    def __init__(self, name, server_id, port, fingerprint):
        addresses = lan_addresses()
        if not addresses:
            raise RuntimeError("No LAN IPv4 address: connect the PC to Wi-Fi or Ethernet first")
        self.zc = Zeroconf(ip_version=IPVersion.V4Only)
        self.info = ServiceInfo(
            "_iotdesign._tcp.local.", f"{name}-{server_id[:6]}._iotdesign._tcp.local.",
            addresses=[socket.inet_aton(ip) for ip in addresses], port=port,
            properties={"server_id": server_id, "api_version": "1", "name": name,
                        "fingerprint": fingerprint},
            server=f"iotdesign-{server_id[:8]}.local.",
        )
        try:
            self.zc.register_service(self.info)
        except Exception:
            self.zc.close()
            raise

    def close(self):
        self.zc.unregister_service(self.info)
        self.zc.close()
