# Wallet alert bot

Watch one EVM address and post alerts to a Discord/Slack-compatible webhook. Stdlib only —
no dependencies, no account, no API key. Works on Base, Ethereum, Arbitrum or any EVM chain.

```bash
RPC_URL=https://mainnet.base.org \
WATCH_ADDRESS=0xYourAddress \
WEBHOOK_URL=https://discord.com/api/webhooks/… \
python wallet_alert_bot.py
```

## What it alerts on

| Mode | Env | Example alert |
|---|---|---|
| Native transfers in/out | always on | `incoming 0.150000 native` |
| ERC-20 transfers | `WATCH_TOKEN=0x…` | `outgoing 1000000 token units (raw)` |
| Balance threshold | `ALERT_BELOW_WEI=…` | `native balance 0.004 below threshold 0.01` |

## Design

- **Polling blocks, not `eth_getLogs` for native.** Native transfers do not emit logs; the bot
  walks new blocks and checks `to`/`from`, which is reliable on low-fee chains.
- **`eth_getLogs` only for ERC-20.** One call per block, filtered by the address, cheaper than
  scanning every receipt.
- **State file.** `wallet-alert-state.json` stores the last scanned block so a restart does not
  replay alerts. Delete it to rescan.
- **Fails soft.** A poll error is logged and the loop continues; the next block batch is retried.
- **Catch-up cap.** `MAX_BLOCKS` (default 50) prevents a long downtime from replaying thousands
  of blocks in one go.

## Config reference

| Var | Required | Default | Meaning |
|---|---|---|---|
| `RPC_URL` | yes | — | JSON-RPC endpoint |
| `WATCH_ADDRESS` | yes | — | address to watch |
| `WEBHOOK_URL` | yes | — | webhook accepting `{"content": "..."}` |
| `WATCH_TOKEN` | no | — | ERC-20 contract for Transfer events |
| `ALERT_BELOW_WEI` | no | — | balance threshold (wei, decimal) |
| `POLL_SECONDS` | no | `15` | poll interval |
| `MAX_BLOCKS` | no | `50` | max blocks per poll (catch-up cap) |
| `START_BLOCK` | no | head | first block to scan |
| `STATE_FILE` | no | `wallet-alert-state.json` | state path |

## Deploy

**Systemd (VPS):**

```ini
[Unit]
Description=Wallet alert bot
After=network-online.target

[Service]
EnvironmentFile=/etc/wallet-alert.env
ExecStart=/usr/bin/python3 /opt/wallet-alert/wallet_alert_bot.py
Restart=always

[Install]
WantedBy=multi-user.target
```

**Docker:**

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY wallet_alert_bot.py .
CMD ["python", "wallet_alert_bot.py"]
```

## Tests

```bash
python -m unittest discover -s test -v
```

The tests cover alert formatting, native transfer scanning with a synthetic block, and the
ERC-20 log parser with synthetic topics/data.
