#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_parser.py - 解析多种代理协议为 Xray-core 客户端配置

支持的协议：
  - VLESS  (vless://uuid@server:port?params&...#name)
  - VMess  (vmess://base64(json))
  - Trojan (trojan://password@server:port?params&...#name)
  - SS     (ss://base64(method:password)@server:port  or  ss://base64(json)  or  ss://method:password@server:port)
  - SOCKS5 (socks5://[user:pass@]server:port)

输出：完整的 Xray-core config dict (outbound 部分)，含 SOCKS5 inbound 供 Playwright 使用
"""

import base64
import json
import re
from urllib.parse import urlparse, parse_qs, unquote


def _safe_b64decode(s: str) -> str:
    """容错 base64 解码（自动补 padding）"""
    s = s.strip().replace("\n", "").replace(" ", "")
    padding = (-len(s)) % 4
    s = s + "=" * padding
    return base64.urlsafe_b64decode(s).decode("utf-8", errors="ignore")


def _qs_get(qs: dict, key: str, default: str = "") -> str:
    """从 parse_qs 的字典取单个值"""
    v = qs.get(key, [default])
    return v[0] if v else default


def parse_vless(uri: str) -> dict:
    """
    vless://uuid@server:port?encryption=none&security=reality&sni=...&type=ws&path=...&host=...#name
    """
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"VLESS URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)

    settings = {}
    stream = {}
    security = _qs_get(qs, "security", "none")
    net_type = _qs_get(qs, "type", "tcp")

    # VMess 的 settings
    settings["id"] = p.username

    # Stream settings
    stream["network"] = net_type

    if net_type == "ws":
        ws_path = _qs_get(qs, "path", "/")
        ws_host = _qs_get(qs, "host", "")
        stream["wsSettings"] = {
            "path": unquote(ws_path),
            "headers": {"Host": ws_host} if ws_host else {},
        }
    elif net_type == "tcp" and _qs_get(qs, "headerType") == "http":
        stream["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {
                    "path": [_qs_get(qs, "path", "/")],
                    "headers": {"Host": [_qs_get(qs, "host", "")]} if _qs_get(qs, "host") else {},
                },
            }
        }
    elif net_type == "grpc":
        stream["grpcSettings"] = {
            "serviceName": _qs_get(qs, "serviceName"),
        }
    elif net_type == "httpupgrade":
        stream["httpupgradeSettings"] = {
            "path": unquote(_qs_get(qs, "path", "/")),
            "host": _qs_get(qs, "host", ""),
        }

    # Security
    if security == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": _qs_get(qs, "sni", p.hostname),
            "allowInsecure": _qs_get(qs, "allowInsecure", "false").lower() == "true",
        }
    elif security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": _qs_get(qs, "sni", ""),
            "fingerprint": _qs_get(qs, "fp", "chrome"),
            "publicKey": _qs_get(qs, "pbk", ""),
            "shortId": _qs_get(qs, "sid", ""),
            "spiderX": _qs_get(qs, "spx", ""),
        }
    else:
        stream["security"] = "none"

    return {
        "protocol": "vless",
        "settings": {"vnext": [{
            "address": p.hostname,
            "port": int(p.port),
            "users": [settings],
        }]},
        "streamSettings": stream,
    }


def parse_vmess(uri: str) -> dict:
    """vmess://base64(json)"""
    if not uri.startswith("vmess://"):
        raise ValueError("not vmess URI")
    b64 = uri[len("vmess://"):]
    try:
        cfg = json.loads(_safe_b64decode(b64))
    except json.JSONDecodeError as e:
        raise ValueError(f"VMess base64 解码失败: {e}")

    # VMess JSON 字段：ps(别名), add, port, id, aid, net, type, host, path, tls, sni, scy
    network = cfg.get("net", "tcp")
    stream = {"network": network}

    if network == "ws":
        stream["wsSettings"] = {
            "path": cfg.get("path", "/"),
            "headers": {"Host": cfg.get("host", "")} if cfg.get("host") else {},
        }
    elif network == "tcp" and cfg.get("type") == "http":
        stream["tcpSettings"] = {
            "header": {
                "type": "http",
                "request": {
                    "path": [cfg.get("path", "/")],
                    "headers": {"Host": [cfg.get("host", "")]} if cfg.get("host") else {},
                },
            }
        }
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": cfg.get("path", "")}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {
            "path": cfg.get("path", "/"),
            "host": cfg.get("host", ""),
        }

    tls = (cfg.get("tls") or "").lower()
    if tls == "tls":
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": cfg.get("sni", cfg.get("add", "")),
            "allowInsecure": (cfg.get("verify") or True) is False if "verify" in cfg else False,
        }
    else:
        stream["security"] = "none"

    return {
        "protocol": "vmess",
        "settings": {"vnext": [{
            "address": cfg.get("add"),
            "port": int(cfg.get("port")),
            "users": [{
                "id": cfg.get("id"),
                "alterId": int(cfg.get("aid", 0)),
                "security": cfg.get("scy", "auto"),
            }],
        }]},
        "streamSettings": stream,
    }


def parse_trojan(uri: str) -> dict:
    """trojan://password@server:port?sni=...&type=ws&path=...&host=...#name"""
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"Trojan URI 缺少 host/port: {uri[:80]}")
    qs = parse_qs(p.query)

    network = _qs_get(qs, "type", "tcp")
    stream = {"network": network}

    if network == "ws":
        stream["wsSettings"] = {
            "path": unquote(_qs_get(qs, "path", "/")),
            "headers": {"Host": _qs_get(qs, "host", "")} if _qs_get(qs, "host") else {},
        }
    elif network == "grpc":
        stream["grpcSettings"] = {"serviceName": _qs_get(qs, "serviceName")}
    elif network == "httpupgrade":
        stream["httpupgradeSettings"] = {
            "path": unquote(_qs_get(qs, "path", "/")),
            "host": _qs_get(qs, "host", ""),
        }

    security = _qs_get(qs, "security", "tls")
    if security == "tls" or security == "":
        stream["security"] = "tls"
        stream["tlsSettings"] = {
            "serverName": _qs_get(qs, "sni", p.hostname),
            "allowInsecure": _qs_get(qs, "allowInsecure", "false").lower() == "true",
        }
    elif security == "reality":
        stream["security"] = "reality"
        stream["realitySettings"] = {
            "serverName": _qs_get(qs, "sni", ""),
            "fingerprint": _qs_get(qs, "fp", "chrome"),
            "publicKey": _qs_get(qs, "pbk", ""),
            "shortId": _qs_get(qs, "sid", ""),
        }
    else:
        stream["security"] = "none"

    return {
        "protocol": "trojan",
        "settings": {"servers": [{
            "address": p.hostname,
            "port": int(p.port),
            "password": unquote(p.username or ""),
        }]},
        "streamSettings": stream,
    }


def parse_ss(uri: str) -> dict:
    """
    ss://base64(method:password)@server:port#name
    ss://base64(json)  (SIP002 with v2ray-plugin)
    ss://method:password@server:port
    """
    if not uri.startswith("ss://"):
        raise ValueError("not ss URI")
    body = uri[len("ss://"):]

    # 处理 # 后的别名
    name = ""
    if "#" in body:
        body, name = body.split("#", 1)
        name = unquote(name)

    # 情况 1: base64(json) 整体编码
    if "@" not in body:
        try:
            decoded = _safe_b64decode(body)
            cfg = json.loads(decoded)
            # v2ray-style: {"v":"2","ps":"...","add":"host","port":"443","id":"...","aid":"0","net":"ws","type":"none","host":"...","path":"/","tls":"tls"}
            if "add" in cfg and "port" in cfg and "id" in cfg:
                # 仿 VMess 格式
                method, password = cfg.get("id", "").split(":", 1) if ":" in cfg.get("id", "") else ("", cfg.get("id", ""))
                return _build_ss_outbound(
                    cfg["add"], int(cfg["port"]),
                    method, password,
                    network=cfg.get("net", "tcp"),
                    path=cfg.get("path", "/"),
                    host=cfg.get("host", ""),
                    tls=(cfg.get("tls") or "").lower() == "tls",
                    sni=cfg.get("sni", cfg.get("host", "")),
                )
        except (json.JSONDecodeError, ValueError):
            pass

    # 情况 2: ss://base64(method:password)@server:port
    if "@" in body:
        userinfo, hostport = body.rsplit("@", 1)
        # 解码 userinfo
        if ":" not in userinfo:
            try:
                userinfo = _safe_b64decode(userinfo)
            except Exception:
                pass
        if ":" not in userinfo:
            raise ValueError(f"SS userinfo 解析失败: {userinfo}")
        method, password = userinfo.split(":", 1)
        # server:port
        if ":" not in hostport:
            raise ValueError(f"SS 缺少 port: {hostport}")
        host, port = hostport.rsplit(":", 1)
        return _build_ss_outbound(host, int(port), method, password)

    raise ValueError(f"SS URI 无法解析: {uri[:80]}")


def _build_ss_outbound(host, port, method, password,
                       network="tcp", path="/", host_header="",
                       tls=False, sni="") -> dict:
    """构造 Shadowsocks outbound"""
    stream = {"network": network}
    if network == "ws":
        stream["wsSettings"] = {
            "path": path,
            "headers": {"Host": host_header} if host_header else {},
        }
        if tls:
            stream["security"] = "tls"
            stream["tlsSettings"] = {"serverName": sni or host, "allowInsecure": False}
        else:
            stream["security"] = "none"
    return {
        "protocol": "shadowsocks",
        "settings": {"servers": [{
            "address": host,
            "port": port,
            "method": method,
            "password": password,
        }]},
        "streamSettings": stream,
    }


def parse_socks5(uri: str) -> dict:
    """socks5://[user:pass@]server:port"""
    p = urlparse(uri)
    if not p.hostname or not p.port:
        raise ValueError(f"SOCKS5 URI 缺少 host/port: {uri[:80]}")
    servers = [{
        "address": p.hostname,
        "port": int(p.port),
    }]
    if p.username:
        servers[0]["users"] = [{
            "user": unquote(p.username),
            "pass": unquote(p.password or ""),
        }]
    return {
        "protocol": "socks",
        "settings": {"servers": servers},
        "streamSettings": {"security": "none"},
    }


# ============ 主入口 ============

PARSERS = {
    "vless://":  parse_vless,
    "vmess://":  parse_vmess,
    "trojan://": parse_trojan,
    "ss://":     parse_ss,
    "socks5://": parse_socks5,
}


def parse_proxy(uri: str) -> dict:
    """解析代理分享链接为 Xray outbound 配置"""
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


def build_xray_config(proxy_outbound: dict,
                      socks_port: int = 1080,
                      http_port: int = 1081) -> dict:
    """构造完整的 Xray-core 配置：本地 SOCKS5+HTTP 入站，远端 outbound"""
    return {
        "log": {
            "loglevel": "warning",  # warning 即可，debug 太多
        },
        "inbounds": [
            {
                "tag": "socks-in",
                "port": socks_port,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"auth": "noauth", "udp": True},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
            {
                "tag": "http-in",
                "port": http_port,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {},
                "sniffing": {"enabled": True, "destOverride": ["http", "tls"]},
            },
        ],
        "outbounds": [
            proxy_outbound,
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {},
            },
            {
                "tag": "block",
                "protocol": "blackhole",
                "settings": {},
            },
        ],
        "routing": {
            "rules": [
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "ip": ["geoip:private"],
                },
                {
                    "type": "field",
                    "outboundTag": "direct",
                    "domain": ["geosite:private"],
                },
            ],
        },
    }


def get_proxy_protocol(uri: str) -> str:
    """返回代理协议名称（用于日志展示）"""
    uri = (uri or "").strip().lower()
    for prefix in PARSERS.keys():
        if uri.startswith(prefix):
            return prefix.rstrip("://")
    return "unknown"


# ============ 测试 ============

if __name__ == "__main__":
    # 简单单元测试
    test_cases = [
        # VLESS + Reality
        "vless://a3f5b8e1-xxxx-xxxx-xxxx-xxxxxxxxxxxx@example.com:443?encryption=none&security=reality&sni=www.microsoft.com&fp=chrome&pbk=abcdef1234567890&sid=1234ab&type=tcp#test-vless",
        # VMess
        "vmess://" + base64.b64encode(json.dumps({
            "v": "2", "ps": "test", "add": "example.com", "port": "443",
            "id": "b831381d-6324-4d53-ad4f-8cda48b30811", "aid": "0",
            "net": "ws", "type": "none", "host": "example.com", "path": "/path",
            "tls": "tls", "sni": "example.com", "scy": "auto",
        }).encode()).decode(),
        # Trojan
        "trojan://password123@example.com:443?sni=example.com&type=ws&path=/ws&host=example.com#test-trojan",
        # SS SIP002
        "ss://" + base64.urlsafe_b64encode(b"aes-256-gcm:password123").decode().rstrip("=") + "@example.com:8388#test-ss",
        # SOCKS5
        "socks5://user:pass@example.com:1080",
        "socks5://example.com:1080",
    ]

    for uri in test_cases:
        print(f"\n=== {uri[:60]}... ===")
        try:
            proto = get_proxy_protocol(uri)
            outbound = parse_proxy(uri)
            print(f"  protocol: {proto}")
            print(f"  xray outbound: {json.dumps(outbound, indent=2)}")
        except Exception as e:
            print(f"  ✗ FAILED: {e}")
