# StockAI over MCP

Point an AI client at your StockAI and ask it what to buy.

MCP (Model Context Protocol) is the standard way an AI assistant reaches a
system it does not own. StockAI speaks it, over the same `sk_live_*` key the REST
API uses, with five tools that **only read**.

```
get_planning_context   which forecast run is active, and at what granularity
get_morning_briefing   what to buy today, with the reason behind each line
get_inventory_status   the stock signal per SKU, filterable by signal or supplier
list_data_sources      what the forecasts are trained on, and how fresh it is
get_training_status    whether a run has finished
```

There is deliberately nothing here that writes. StockAI will not upload your data,
start a training run or record a purchase order through this connection — those
stay on the REST API, where a person triggers them and can see what happened.
The reason is in `docs/assistant-actions.md`: an AI client cannot show you a
before/after card and cannot hold an undo token, so it is given nothing that
would need one.

## Getting a key

In StockAI: **Automatización → API Keys → Generar key**. **Read only** is enough
for everything on this page. Name it after the thing that will use it
("claude-desktop"), not after a person — the key keeps working when they leave
and does not gain permissions if they are promoted.

The key is shown once. There is no copy of it anywhere, including here.

## Two ways to connect

### A. Remote — a client that speaks HTTP

Most hosted clients take a URL and a header. Give them:

```
URL      https://your-instance/api/v1/mcp
Header   Authorization: Bearer sk_live_...
```

That is the whole setup. The server is stateless: there is no session to
establish, nothing to keep alive, and any instance behind your load balancer can
answer any request.

### B. Local — Claude Desktop and anything else that speaks stdio

Desktop clients launch a process and talk to it over stdin/stdout. `stockai_mcp.py`
is that process. It is a **pipe**: it forwards frames to the URL above and
writes back what comes out. It holds no catalogue and no logic, so a tool fixed
on the server needs no new copy of this file on anybody's machine.

Standard library only — no `pip install`, no virtualenv. Copy the file, set two
variables:

```json
{
  "mcpServers": {
    "stockai": {
      "command": "python",
      "args": ["/absolute/path/to/stockai_mcp.py"],
      "env": {
        "STOCKAI_URL": "https://your-instance",
        "STOCKAI_API_KEY": "sk_live_..."
      }
    }
  }
}
```

`STOCKAI_URL` is the instance root, with no trailing slash and **without**
`/api/v1` — the script appends the path itself.

## Two things to know before you trust an answer

**`SIN_DATOS` is not "fine".** It means there is no stock on record for that
SKU. Not knowing and being well stocked are different facts, and StockAI keeps them
different on purpose. An assistant that treats them the same will tell you
everything is covered while something runs out.

**Check `*_source` before quoting a number.** Every row says where its lead
time, unit cost and MOQ came from: `user` (you typed it), `file` (your own
export), `supplier_rule`, `learned` (from your receptions) or `default` — which
means StockAI had nothing and assumed. A `default` is StockAI's guess, not your data,
and is worth saying out loud when you report it.

## Answers are capped, and say so

`get_inventory_status` returns the most urgent rows first, not the first rows it
finds, and never silently cuts. When there is more than fits it sets
`truncated`, reports `total_matching_items`, and — the part that matters — the
`summary` counts **all** of them. A summary that shrank with the page would tell
your assistant there are four SKUs to order when there are three hundred.

Narrow with `signal` or `supplier` rather than raising `limit`.

And when a filter matches nothing, the answer says so in `empty_reason` instead
of handing back a clean empty list. A supplier name spelled the way your ERP
spells it rather than the way StockAI stores it is the common case, and without
that field it looks identical to a catalogue with nothing to order.

## Limits and what is recorded

The same **120 calls per minute per key** as the REST API; over it, `429` with
`Retry-After`. Reads are not written to the activity log — at that rate they
would bury what matters — so the trail of an AI client's questions is on your
side, not in StockAI.

## When it does not work

| What you see | What it is |
|---|---|
| The connector never appears in the client | Wrong URL. It ends in `/api/v1/mcp`, and `STOCKAI_URL` must NOT already include `/api/v1` |
| `401` / "Check STOCKAI_API_KEY" | The key is wrong, revoked or expired. Generate another; revoke the old one from the same screen |
| `429` | Over 120 calls a minute. The client is looping — the answer is almost never a higher ceiling |
| `405` on a GET | Expected. This server is stateless and offers no server-to-client stream; everything comes back on the POST |
| "No completed session for this tenant yet" | Nothing has been trained. That is a real answer, not a failure — train a forecast in the app first |
| "answered 200 with something that is not JSON" | `STOCKAI_URL` points at a proxy or an SSO portal rather than at StockAI. The adapter refuses to pass that into the protocol stream |

Start the desktop adapter by hand to see what it is doing; everything it says
goes to stderr, never to stdout:

```sh
STOCKAI_URL=https://your-instance STOCKAI_API_KEY=sk_live_... python stockai_mcp.py
```

Then paste a frame and press enter:

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list"}
```
