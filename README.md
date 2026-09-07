# ComfyUI FLUX.2 (BFL API)

ComfyUI custom nodes that call the [Black Forest Labs](https://api.bfl.ai) FLUX.2 API
directly, so FLUX.2 [pro], [max], [flex] and [klein] results drop straight into a local
graph alongside `SaveImage`, `PreviewImage`, or another edit node.

Everything here is generated against the official OpenAPI description
(`bfl_openapi.json`, fetched from `https://api.bfl.ai/openapi.json`) — endpoint paths,
field names, ranges and defaults all come from that document.

## Nodes

| Node | Endpoint | Reference images | Extra controls |
| --- | --- | --- | --- |
| FLUX.2 [pro] (BFL API) | `/v1/flux-2-pro` | up to 8 | `disable_pup` |
| FLUX.2 [max] (BFL API) | `/v1/flux-2-max` | up to 8 | `disable_pup` |
| FLUX.2 [flex] (BFL API) | `/v1/flux-2-flex` | up to 8 | `prompt_upsampling`, `steps` (1–50), `guidance` (1.5–10) |
| FLUX.2 [klein] 9B (BFL API) | `/v1/flux-2-klein-9b` | up to 4 | — |
| FLUX.2 [klein] 4B (BFL API) | `/v1/flux-2-klein-4b` | up to 4 | — |

They all live under the **BFL/FLUX.2** category and output a single `IMAGE`.

The klein schema (`Flux2KleinInputs`) defines neither `disable_pup` nor
`prompt_upsampling`, so those nodes deliberately have no upsampling widget.

## Install

Clone into ComfyUI's `custom_nodes` directory and install the dependencies with the same
Python that runs ComfyUI:

```bash
cd <ComfyUI>/custom_nodes
git clone <this repo> comfyui-flux2-bfl
pip install -r comfyui-flux2-bfl/requirements.txt
```

`custom_nodes` for ComfyUI Desktop is:

- **Windows** — `%APPDATA%\ComfyUI\custom_nodes` (the install picks the location on first
  run; check *Settings → Server Config* if you moved it)
- **macOS** — `~/Library/Application Support/ComfyUI/custom_nodes`
- **Linux** — `~/.config/ComfyUI/custom_nodes`

Restart ComfyUI afterwards so the nodes and the frontend extension both load.

## API key

Get a key from the [BFL dashboard](https://dashboard.bfl.ai) and expose it as
`BFL_API_KEY` before ComfyUI starts:

```bash
export BFL_API_KEY="bfl-..."          # Linux / macOS shell
setx BFL_API_KEY "bfl-..."            # Windows, then restart ComfyUI Desktop
```

If the variable is unset, each node's `api_key` widget is used instead. That widget is
saved into the workflow JSON in plain text, so prefer the environment variable for
anything you intend to share.

## Reference images

Each node starts with one optional `input_image` socket. Connect it and an
`input_image_2` socket appears; keep going up to the node's maximum (8, or 4 for klein).
Disconnecting the last image frees the trailing sockets again. Only the connected slots
are sent, keyed to the exact field names the API expects.

Reference images are encoded as base64 PNG. Results come back as a signed URL that
expires after 10 minutes, so the node downloads it the moment the task reports `Ready`.

## Parameters

- **prompt** — the only field the API requires.
- **seed** — `-1` picks a fresh random seed locally for each run; any other value is sent
  verbatim.
- **width / height** — `0` lets the API match the reference image (or choose its own size
  for text-to-image) and is omitted from the request. Any other value must be at least 64.
- **safety_tolerance** — `0` is the strictest moderation setting, `5` the most permissive.
- **output_format** — `jpeg`, `png` or `webp`.
- **steps / guidance** — flex only.
- **disable_pup** — pro and max only; the API upsamples prompts by default, so enable this
  to use the prompt exactly as written.

`webhook_url` and `webhook_secret` are not exposed; the nodes poll synchronously.

## Errors

Validation failures (422) are surfaced with the API's own `detail` messages, field by
field. The two moderation states are reported distinctly — `Request Moderated` when the
prompt is refused before generation, `Content Moderated` when the finished image is
blocked — rather than as a generic failure. Polling gives up after 10 minutes and honours
ComfyUI's cancel button.
