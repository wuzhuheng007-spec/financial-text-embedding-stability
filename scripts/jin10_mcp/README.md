# Jin10 MCP local intake engine

This code does not run on import or at system startup. It requires a real
`initialize` / `notifications/initialized` / `tools/list` exchange, then a
private `jin10_confirmed_tool_shape_v1` JSON that binds the selected tool's
actual `inputSchema` hash and an actual response witness hash. Parameters and
response paths are never inferred from the historical project note.

Keep the SQLite database, confirmed shape, raw probe replies, quota evidence
and runtime receipts under the ignored repository `artifacts/` directory.
Do not publish provider text, bearer credentials, session IDs or the database.
`source_id_stability_confirmed` and `url_semantics_confirmed` default to false;
set them true only after inspecting the provider's actual field semantics.

## Manual commands

From the repository root, after a private shape file has been confirmed:

```text
python -m scripts.jin10_mcp.cli status --stream flash --db artifacts/jin10/jin10.sqlite
python -m scripts.jin10_mcp.cli start --stream flash --kind FLASH --db artifacts/jin10/jin10.sqlite --shape artifacts/jin10/flash_shape.json
python -m scripts.jin10_mcp.cli stop --stream flash --db artifacts/jin10/jin10.sqlite
python -m scripts.jin10_mcp.cli resume --stream flash --kind FLASH --db artifacts/jin10/jin10.sqlite --shape artifacts/jin10/flash_shape.json
```

`start` begins at the front of the selected feed; `resume` replays the last
complete page before continuing. Both require `JIN10_MCP_TOKEN` in the current
process. The default unverified-quota gate permits at most two flash requests
or one calendar request per command, with a two-minute runtime bound. The
confirmed input schema and actual field paths govern all tool arguments.

To import already authorized probe calls **without another network request**:

```text
python -m scripts.jin10_mcp.cli ingest-probe --stream flash --kind FLASH --db artifacts/jin10/jin10.sqlite --shape artifacts/jin10/flash_shape.json --tool-list-file artifacts/jin10/tools_list_private.json --capture-file artifacts/jin10/flash_probe_pages_private.json
```

The capture file is an array of actual page records with `arguments`,
`response`, `http_status` and `received_at_utc`. The importer preserves those
times, raw JSON-RPC replies and cursor chain. It records a coverage gap if a
response is partial or pagination remains. Do not re-label the offline import
as a fresh API call.

An optional `trial` command accepts `--hours` (at most 24) and a private
`--quota-policy` file whose official source, verified status, minimum interval
between every HTTP request, minimum interval between polls and total HTTP-request
budget were checked first. The gate counts initialize, initialized, tools/list
and tool calls, including failed HTTP attempts. It stops on its time/request
limit, a manual `stop`, auth/rate/transport failure or a page error. No quota
policy is present in this repository, so it cannot run an unattended trial
from the committed files.

The local display adapter is
`export_display(db_path, limit=50, include_private_content=False)`. Its
default output contains metadata and acquisition status only. Setting
`include_private_content=True` exposes latest provider title/body and calendar
actual/consensus/previous/revised fields for an **ignored local page only**;
the values remain `SOURCE_REPORT` and a current fetch never proves a historic
pre-release consensus snapshot.
