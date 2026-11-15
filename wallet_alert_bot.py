#!/usr/bin/env python3
"""Wallet alert bot: watch one EVM address and post alerts to a webhook.

Stdlib only. Works against any EVM chain with a JSON-RPC endpoint (Base,
Ethereum, Arbitrum, …). Posts to a Discord/Slack-compatible webhook URL.

Watch modes
  - incoming/outgoing native transfers involving WATCH_ADDRESS
  - ERC-20 Transfer events to/from WATCH_ADDRESS (WATCH_TOKEN, optional)
  - balance threshold (ALERT_BELOW_WEI, optional)

Config via environment variables:
  RPC_URL          JSON-RPC endpoint (required)
  WATCH_ADDRESS    0x… address to watch (required)
  WEBHOOK_URL      webhook that accepts {"content": "..."} (required)
  WATCH_TOKEN      optional ERC-20 contract to track Transfer events
  ALERT_BELOW_WEI  optional: alert when native balance drops below this
  POLL_SECONDS     default 15
  START_BLOCK      optional: block to start from (default: latest)
  STATE_FILE       default: wallet-alert-state.json

Run:
  python wallet_alert_bot.py            # loop
  python wallet_alert_bot.py --once     # one poll, then exit
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass, asdict

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"


@dataclass
class Alert:
    kind: str          # native | token | balance
    chain_id: int
    block: int
    tx_hash: str
    address: str
    detail: str

    def to_text(self) -> str:
        return (f"**Wallet alert · {self.kind}**\n"
                f"block {self.block} · chain {self.chain_id}\n"
                f"tx `{self.tx_hash}`\n"
                f"{self.detail}\n"
                f"address `{self.address}`")


def rpc(url: str, method: str, params: list, timeout: int = 30):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "wallet-alert-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    if "error" in data:
        raise RuntimeError(f"rpc {method}: {data['error']}")
    return data.get("result")


def post_webhook(url: str, text: str, timeout: int = 20) -> int:
    body = json.dumps({"content": text}).encode()
    req = urllib.request.Request(url, data=body,
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "wallet-alert-bot/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def scan_native(rpc_url: str, address: str, block: dict) -> list[Alert]:
    """Native transfers involving `address` inside one block."""
    alerts: list[Alert] = []
    addr = address.lower()
    for tx in block.get("transactions") or []:
        if not isinstance(tx, dict):
            continue
        to = (tx.get("to") or "").lower()
        frm = (tx.get("from") or "").lower()
        if to == addr or frm == addr:
            value = int(tx.get("value") or "0x0", 16)
            if value == 0:
                continue
            direction = "incoming" if to == addr else "outgoing"
            alerts.append(Alert("native", 0, int(block["number"], 16), tx["hash"], address,
                                f"{direction} {value / 1e18:.6f} native"))
    return alerts


def scan_token(rpc_url: str, address: str, token: str, block_number: int) -> list[Alert]:
    """ERC-20 Transfer events involving `address` in one block."""
    padded = "0x" + "0" * 24 + address.lower()[2:]
    logs = rpc(rpc_url, "eth_getLogs", [{
        "fromBlock": hex(block_number), "toBlock": hex(block_number),
        "address": token, "topics": [TRANSFER_TOPIC],
    }])
    alerts: list[Alert] = []
    for log in logs or []:
        topics = log.get("topics") or []
        if len(topics) < 3:
            continue
        from_addr = "0x" + topics[1][-40:]
        to_addr = "0x" + topics[2][-40:]
        if address.lower() in (from_addr, to_addr):
            raw = int(log.get("data") or "0x0", 16)
            direction = "incoming" if to_addr == address.lower() else "outgoing"
            alerts.append(Alert("token", 0, int(log["blockNumber"], 16), log["transactionHash"],
                                address, f"{direction} {raw} token units (raw) from `{token}`"))
    return alerts


def balance_alert(rpc_url: str, address: str, threshold_wei: int, block_number: int) -> list[Alert]:
    balance = int(rpc(rpc_url, "eth_getBalance", [address, "latest"]), 16)
    if balance < threshold_wei:
        return [Alert("balance", 0, block_number, "-", address,
                      f"native balance {balance / 1e18:.6f} below threshold {threshold_wei / 1e18:.6f}")]
    return []


def load_state(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001
        return {}


def save_state(path: str, state: dict) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(state, fh, indent=2)


def run_once(cfg: dict, state: dict) -> tuple[list[Alert], dict]:
    rpc_url = cfg["rpc_url"]
    address = cfg["address"]
    chain_id = int(rpc(rpc_url, "eth_chainId", []), 16)
    head = int(rpc(rpc_url, "eth_blockNumber", []), 16)
    start = int(state.get("last_block", cfg.get("start_block") or head))
    first = max(start, head - cfg.get("max_blocks", 50))
    alerts: list[Alert] = []
    for bn in range(first + 1, head + 1):
        block = rpc(rpc_url, "eth_getBlockByNumber", [hex(bn), True])
        if not block:
            continue
        alerts.extend(scan_native(rpc_url, address, block))
        if cfg.get("token"):
            alerts.extend(scan_token(rpc_url, address, cfg["token"], bn))
        if cfg.get("below_wei") is not None:
            alerts.extend(balance_alert(rpc_url, address, int(cfg["below_wei"]), bn))
        state["last_block"] = bn
    for alert in alerts:
        alert.chain_id = chain_id
    return alerts, state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    cfg = {
        "rpc_url": os.environ.get("RPC_URL", ""),
        "address": os.environ.get("WATCH_ADDRESS", ""),
        "webhook": os.environ.get("WEBHOOK_URL", ""),
        "token": os.environ.get("WATCH_TOKEN") or None,
        "below_wei": os.environ.get("ALERT_BELOW_WEI") or None,
        "poll": int(os.environ.get("POLL_SECONDS", "15")),
        "start_block": os.environ.get("START_BLOCK") or None,
        "max_blocks": int(os.environ.get("MAX_BLOCKS", "50")),
    }
    missing = [k for k in ("rpc_url", "address", "webhook") if not cfg[k]]
    if missing:
        print("missing env: " + ", ".join(missing), file=sys.stderr)
        return 2

    state_path = os.environ.get("STATE_FILE", "wallet-alert-state.json")
    state = load_state(state_path)
    print(f"watching {cfg['address']} via {cfg['rpc_url']}")
    while True:
        try:
            alerts, state = run_once(cfg, state)
            save_state(state_path, state)
            for alert in alerts:
                print(json.dumps(asdict(alert)))
                post_webhook(cfg["webhook"], alert.to_text())
        except Exception as exc:  # noqa: BLE001 - keep the loop alive
            print(f"poll error: {exc}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(cfg["poll"])


if __name__ == "__main__":
    raise SystemExit(main())
