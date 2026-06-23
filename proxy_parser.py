#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_parser.py - 解析多种代理协议为 sing-box 客户端配置

支持的协议（7 种）：
  - VLESS  (vless://uuid@server:port?security=reality&sni=...&type=ws&...)
  - VMess  (vmess://base64(json))
  - Trojan (trojan://password@server:port?sni=...&type=ws&...)
  - Shadowsocks (ss://base64(method:password)@server:port  or  ss://base64(json))
  - SOCKS5 (socks5://[user:pass@]server:port)
  - Hysteria2 (hysteria2://auth@server:port?sni=...&insecure=0#name)
  - TUIC (tuic://uuid:password@server:port?sni=...&congestion_control=bbr#name)

输出：完整的 sing-box config dict (含 SOCKS5 入站 + outbound + 直连路由)
"""

import base64
import json
from urllib.parse import urlparse, parse_qs, unquote


def _safe_b64decode(s: str) -> str:
    s = s.strip().replace("\n", "").replace(" ", "")
    padding = (-len(s)) % 4
    s = s + "=" * padding
    try:
        return base64.urlsafe_b64decode(s).decode("utf-8", errors="ignore")
    except Exception:
        return base64.b64decode(s).decode("utf-8", errors="ignore")


def _qs_get(qs: dict, key: str, default: str = "") -> str:
    v = qs.get(key, [default])
    return v[0] if v else default


def _tls_settings(qs: dict, hostname: str) -> dict:
    """从 query string 生成 sing-box TLS 配置"""
    sni = _qs_get(qs, "sni", hostname)
    alpn_str = _qs_get(qs, "alpn", "")
    insecure = _qs_get(qs, "allowInsecure", _qs_get(qs, "insecure", "0"))
    tls = {
        "enabled": True,
        "server_name": sni,
        "insecure": insecure.lower() in ("1", "true", "yes"),
    }
    if alpn_str:
        tls["alpn"] = alpn_str.split(",")
    fp = _qs_get(qs, "fp", "")
    if fp:
        tls["utls"] = {"enabled": True, "fingerprint": fp}
    return tls


def _reality_settings(qs: dict) -> dict:
    """生成 sing-box Reality TLS 配置"""
    return {
        "enabled": True,
        "server_name": _qs_get(qs, "sni", ""),
        "reality": {
            "enabled": True,
            "public_key": _qs_get(qs, "pbk", ""),
            "short_id": _qs_get(qs, "sid", ""),
        },
        "utls": {
            "enabled": True,
            "fingerprint": _qs_get(qs, "fp", "chrome"),
        },
    }


def _transport_settings(qs: dict, cfg: dict) -> dict:
    """根据 net type 生成 sing-box transport 配置（适用于 vless/vmess/trojan）"""
    net_type = _qs_get(qs, "type", _qs_get(qs, "net", "tcp"))
    transport = {}
    if net_type == "ws":
        transport = {
            "type": "ws",
            "path": unquote(_qs_get(qs, "path", "/")),
        }
        host = _qs_get(qs, "host", "")
        if host:
            transport["headers"] = {"Host": host}
    elif net_type == "grpc":
        transport = {
            "type": "grpc",
            "service_name": _qs_get(qs, "serviceName", _qs_get(qs, "path", "")),
        }
    elif net_type == "httpupgrade":
        transport = {
            "type": "httpupgrade",
            "path": unquote(_qs_get(qs, "path", "/")),
            "host": _qs_get(qs, "host", ""),
        }
    elif net_type == "http" or (net_type == "tcp" and _qs_get(qs, "headerType") == "http"):
        transport = {
            "type": "http",
            "path": unquote(_qs_get(qs, "path", "/")),
            "host": [_qs_get(qs, "host", "")] if _qs_get(qs, "host") else [],
        }
    return transport


def parse_vless(uri: str) -> dict:
    """vless://uuid@server:port?encryption=none&security=reality&sni=...&type=ws&path=...&host=...#name"""
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"VLESS URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)
    security = _qs_get(qs, "security", "none")

    outbound = {
        "type": "vless",
        "tag": "proxy",
        "server": p.hostname,
        "server_port": int(p.port),
        "uuid": p.username,
        "flow": _qs_get(qs, "flow", ""),
    }
    if not outbound["flow"]:
        outbound.pop("flow")

    if security == "reality":
        outbound["tls"] = _reality_settings(qs)
    elif security == "tls":
        outbound["tls"] = _tls_settings(qs, p.hostname)

    transport = _transport_settings(qs, {})
    if transport:
        outbound["transport"] = transport
    return outbound


def parse_vmess(uri: str) -> dict:
    """vmess://base64(json)"""
    if not uri.startswith("vmess://"):
        raise ValueError("not vmess URI")
    cfg = json.loads(_safe_b64decode(uri[len("vmess://"):]))

    network = cfg.get("net", "tcp")
    outbound = {
        "type": "vmess",
        "tag": "proxy",
        "server": cfg.get("add"),
        "server_port": int(cfg.get("port")),
        "uuid": cfg.get("id"),
        "alter_id": int(cfg.get("aid", 0)),
        "security": cfg.get("scy", "auto"),
    }

    tls = (cfg.get("tls") or "").lower()
    if tls == "tls":
        sni = cfg.get("sni", cfg.get("add", ""))
        outbound["tls"] = {
            "enabled": True,
            "server_name": sni,
            "insecure": not bool(cfg.get("verify", True)),
        }
        if cfg.get("alpn"):
            outbound["tls"]["alpn"] = cfg["alpn"].split(",")

    # 构造伪 qs 用 _transport_settings
    pseudo_qs = {
        "type": [network],
        "path": [cfg.get("path", "/")],
        "host": [cfg.get("host", "")],
        "serviceName": [cfg.get("path", "")],  # grpc
    }
    transport = _transport_settings(pseudo_qs, {})
    if transport:
        outbound["transport"] = transport
    return outbound


def parse_trojan(uri: str) -> dict:
    """trojan://password@server:port?sni=...&type=ws&path=...&host=...#name"""
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"Trojan URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)
    security = _qs_get(qs, "security", "tls")  # trojan 默认 TLS

    outbound = {
        "type": "trojan",
        "tag": "proxy",
        "server": p.hostname,
        "server_port": int(p.port),
        "password": unquote(p.username or ""),
    }

    if security == "reality":
        outbound["tls"] = _reality_settings(qs)
    elif security == "tls" or security == "":
        outbound["tls"] = _tls_settings(qs, p.hostname)

    transport = _transport_settings(qs, {})
    if transport:
        outbound["transport"] = transport
    return outbound


def parse_ss(uri: str) -> dict:
    """ss://base64(method:password)@server:port  /  ss://base64(json)  /  ss://method:password@server:port"""
    if not uri.startswith("ss://"):
        raise ValueError("not ss URI")
    body = uri[len("ss://"):]

    # 去掉别名
    if "#" in body:
        body = body.split("#", 1)[0]

    # 情况 1: 整体 base64(json)
    if "@" not in body:
        try:
            decoded = _safe_b64decode(body)
            cfg = json.loads(decoded)
            if "add" in cfg and "port" in cfg and "id" in cfg:
                userinfo = cfg["id"]
                if ":" not in userinfo:
                    return None
                method, password = userinfo.split(":", 1)
                return _build_ss_outbound(
                    cfg["add"], int(cfg["port"]), method, password,
                    network=cfg.get("net", "tcp"),
                    path=cfg.get("path", "/"),
                    host=cfg.get("host", ""),
                    tls_enabled=(cfg.get("tls") or "").lower() == "tls",
                    sni=cfg.get("sni", cfg.get("host", "")),
                )
        except (json.JSONDecodeError, ValueError):
            pass

    # 情况 2: ss://base64(method:password)@server:port
    if "@" in body:
        userinfo, hostport = body.rsplit("@", 1)
        if ":" not in userinfo:
            try:
                userinfo = _safe_b64decode(userinfo)
            except Exception:
                pass
        if ":" not in userinfo:
            raise ValueError(f"SS userinfo 解析失败: {userinfo}")
        method, password = userinfo.split(":", 1)
        if ":" not in hostport:
            raise ValueError(f"SS 缺少 port: {hostport}")
        host, port = hostport.rsplit(":", 1)
        # 检查 SIP002 with plugin
        if "?" in port:
            port, plugin_qs = port.split("?", 1)
            plugin_qs = parse_qs(plugin_qs)
        return _build_ss_outbound(host, int(port), method, password)

    raise ValueError(f"SS URI 无法解析: {uri[:80]}")


def _build_ss_outbound(host, port, method, password,
                       network="tcp", path="/", host_header="",
                       tls_enabled=False, sni="") -> dict:
    outbound = {
        "type": "shadowsocks",
        "tag": "proxy",
        "server": host,
        "server_port": port,
        "method": method,
        "password": password,
    }
    if network == "ws":
        outbound["transport"] = {
            "type": "ws",
            "path": path,
        }
        if host_header:
            outbound["transport"]["headers"] = {"Host": host_header}
        if tls_enabled:
            outbound["tls"] = {
                "enabled": True,
                "server_name": sni or host,
                "insecure": False,
            }
    elif network == "grpc":
        outbound["transport"] = {"type": "grpc", "service_name": path}
    return outbound


def parse_socks5(uri: str) -> dict:
    """socks5://[user:pass@]server:port"""
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"SOCKS5 URI 缺少 host/port: {uri[:80]}")
    outbound = {
        "type": "socks",
        "tag": "proxy",
        "server": p.hostname,
        "server_port": int(p.port),
        "version": "5",
    }
    if p.username:
        outbound["username"] = unquote(p.username)
        outbound["password"] = unquote(p.password or "")
    return outbound


def parse_hysteria2(uri: str) -> dict:
    """
    hysteria2://auth@server:port?sni=...&insecure=0&pinSHA256=...#name
    或 hysteria2://auth@server:port/?obfs=salamander&obfs-password=xxx&sni=...
    """
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"Hysteria2 URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)

    outbound = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": p.hostname,
        "server_port": int(p.port),
        "password": unquote(p.username or ""),
        "up_mbps": int(_qs_get(qs, "up", "0")) or None,
        "down_mbps": int(_qs_get(qs, "down", "0")) or None,
    }
    # 去掉 None
    outbound = {k: v for k, v in outbound.items() if v is not None}

    # TLS 配置（Hysteria2 必须用 TLS）
    sni = _qs_get(qs, "sni", p.hostname)
    insecure = _qs_get(qs, "insecure", "0")
    alpn = _qs_get(qs, "alpn", "h3")
    outbound["tls"] = {
        "enabled": True,
        "server_name": sni,
        "insecure": insecure.lower() in ("1", "true", "yes"),
        "alpn": alpn.split(",") if alpn else ["h3"],
    }

    # Obfs
    obfs = _qs_get(qs, "obfs", "")
    if obfs:
        outbound["obfs"] = {
            "type": obfs,
            "password": _qs_get(qs, "obfs-password", ""),
        }

    return outbound


def parse_tuic(uri: str) -> dict:
    """
    tuic://uuid:password@server:port?sni=...&congestion_control=bbr&alpn=h3&allow_insecure=0#name
    """
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"TUIC URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)

    # tuic 的 userinfo 是 uuid:password
    username = unquote(p.username or "")
    password = unquote(p.password or "")
    if ":" in username:
        # 实际是 uuid:password 都在 username 里
        uuid, password = username.split(":", 1)

    outbound = {
        "type": "tuic",
        "tag": "proxy",
        "server": p.hostname,
        "server_port": int(p.port),
        "uuid": username if ":" not in username else uuid,
        "password": password,
        "congestion_control": _qs_get(qs, "congestion_control", _qs_get(qs, "congestionControl", "bbr")),
        "udp_relay_mode": _qs_get(qs, "udp_relay_mode", _qs_get(qs, "udpRelayMode", "native")),
        "zero_rtt_handshake": _qs_get(qs, "zero_rtt_handshake", _qs_get(qs, "zeroRttHandshake", "0")).lower() in ("1", "true", "yes"),
        "heartbeat": "10s",
    }

    # TLS
    sni = _qs_get(qs, "sni", p.hostname)
    insecure = _qs_get(qs, "allow_insecure", _qs_get(qs, "insecure", _qs_get(qs, "allowInsecure", "0")))
    alpn = _qs_get(qs, "alpn", "h3")
    outbound["tls"] = {
        "enabled": True,
        "server_name": sni,
        "insecure": insecure.lower() in ("1", "true", "yes"),
        "alpn": alpn.split(",") if alpn else ["h3"],
    }

    return outbound


# ============ 主入口 ============

PARSERS = {
    "vless://":     parse_vless,
    "vmess://":     parse_vmess,
    "trojan://":    parse_trojan,
    "ss://":        parse_ss,
    "socks5://":    parse_socks5,
    "hysteria2://": parse_hysteria2,
    "hy2://":       parse_hysteria2,  # 别名
    "tuic://":      parse_tuic,
}


def parse_proxy(uri: str) -> dict:
    """解析代理分享链接为 sing-box outbound 配置"""
    uri = (uri or "").strip()
    if not uri:
        raise ValueError("代理 URI 为空")

    for prefix, parser in PARSERS.items():
        if uri.startswith(prefix):
            return parser(uri)

    raise ValueError(
        f"不支持的代理协议，仅支持: {list(PARSERS.keys())}\n"
        f"收到的 URI: {uri[:50]}..."
    )


def build_singbox_config(proxy_outbound: dict, socks_port: int = 1080) -> dict:
    """构造完整的 sing-box config"""
    return {
        "log": {
            "level": "info",
            "timestamp": True,
        },
        "dns": {
            "servers": [
                {"tag": "google", "address": "tls://8.8.8.8"},
                {"tag": "local", "address": "223.5.5.5", "detour": "direct"},
            ],
            "rules": [
                {"outbound": "any", "server": "local"},
            ],
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {
                "type": "socks",
                "tag": "socks-in",
                "listen": "127.0.0.1",
                "listen_port": socks_port,
                "udp_timeout": "300s",
            },
            {
                "type": "mixed",
                "tag": "mixed-in",
                "listen": "127.0.0.1",
                "listen_port": socks_port + 1,
            },
        ],
        "outbounds": [
            proxy_outbound,
            {
                "type": "direct",
                "tag": "direct",
            },
            {
                "type": "block",
                "tag": "block",
            },
            {
                "type": "dns",
                "tag": "dns-out",
            },
        ],
        "route": {
            "rules": [
                {
                    "protocol": "dns",
                    "outbound": "dns-out",
                },
                {
                    "ip_is_private": True,
                    "outbound": "direct",
                },
            ],
            "final": "proxy",
        },
    }


def get_proxy_protocol(uri: str) -> str:
    uri = (uri or "").strip().lower()
    for prefix in PARSERS.keys():
        if uri.startswith(prefix):
            return prefix.rstrip("://")
    return "unknown"


# 向后兼容：旧代码可能调用 build_xray_config
def build_xray_config(proxy_outbound: dict, socks_port: int = 1080, http_port: int = 1081) -> dict:
    """[已弃用] 别名，调用 build_singbox_config"""
    return build_singbox_config(proxy_outbound, socks_port)


# ============ 测试 ============

if __name__ == "__main__":
    test_cases = [
        ("VLESS+Reality", "vless://a3f5b8e1-xxxx-xxxx-xxxx-xxxxxxxxxxxx@example.com:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=abcdef1234567890&sid=1234ab&type=tcp#test-vless"),
        ("VLESS+WS+TLS", "vless://a3f5b8e1-xxxx-xxxx-xxxx-xxxxxxxxxxxx@example.com:443?encryption=none&security=tls&sni=example.com&type=ws&path=/ws&host=example.com#test"),
        ("VMess", "vmess://" + base64.b64encode(json.dumps({
            "v": "2", "ps": "test", "add": "example.com", "port": "443",
            "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0",
            "net": "ws", "type": "none", "host": "example.com", "path": "/path",
            "tls": "tls", "sni": "example.com", "scy": "auto",
        }).encode()).decode()),
        ("Trojan", "trojan://password123@example.com:443?sni=example.com&type=ws&path=/ws&host=example.com#test-trojan"),
        ("Shadowsocks", "ss://" + base64.urlsafe_b64encode(b"aes-256-gcm:password123").decode().rstrip("=") + "@example.com:8388#test-ss"),
        ("SOCKS5+auth", "socks5://user:pass@example.com:1080"),
        ("SOCKS5 no auth", "socks5://example.com:1080"),
        ("Hysteria2", "hysteria2://auth_secret@example.com:443?sni=example.com&insecure=0&alpn=h3#hy2"),
        ("Hysteria2+obfs", "hysteria2://auth_secret@example.com:443?obfs=salamander&obfs-password=xxx&sni=example.com&insecure=1#hy2"),
        ("hy2 alias", "hy2://auth_secret@example.com:443?sni=example.com#hy2"),
        ("TUIC", "tuic://b831381d-6324-4d53-ad4f-8cda48b30811:password123@example.com:443?sni=example.com&congestion_control=bbr&alpn=h3&allow_insecure=0#tuic"),
        ("TUIC v5", "tuic://b831381d-6324-4d53-ad4f-8cda48b30811:password123@example.com:443?sni=example.com&udp_relay_mode=native&zero_rtt_handshake=1#tuic"),
    ]

    for name, uri in test_cases:
        print(f"\n{'=' * 70}")
        print(f"=== {name} ===")
        print(f"{'=' * 70}")
        print(f"  URI: {uri[:70]}...")
        try:
            proto = get_proxy_protocol(uri)
            outbound = parse_proxy(uri)
            print(f"  protocol: {proto}")
            print(f"  sing-box outbound:")
            print(json.dumps(outbound, indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
            import traceback
            traceback.print_exc()
