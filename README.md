# strange-pets — ComfyUI image API nodes

ComfyUI custom nodes that call hosted image APIs directly, so results drop straight into a
local graph alongside `SaveImage`, `PreviewImage`, or another edit node. Two APIs so far:

- **[Black Forest Labs](https://api.bfl.ai) FLUX.2** — pro, max, flex and klein, plus a
  batch/sweep node. 7 nodes.
- **[Stability AI](https://api.stability.ai) Stable Image (v2beta)** — Generate, Edit,
  Upscale and Control. 17 nodes.
- **Utilities** — aspect ratios, endpoint-limit fitting, directory load/save with filename
  templating and metadata embedding, contact sheets, prompt/seed lists, and key/cost/cache
  tooling. 12 nodes.

Everything is generated against the official OpenAPI descriptions shipped alongside the
code (`bfl_openapi.json` and `stability-openapi.json`) — endpoint paths, field names,
ranges and defaults all come from those documents rather than from memory.

All 36 nodes are categorised under **strange-pets/** in the node browser, sub-grouped by
vendor (`strange-pets/BFL/FLUX.2`, `strange-pets/StabAI/Generate`, and so on).

## FLUX.2 nodes (BFL)

| Node | Endpoint | Reference images | Extra controls |
| --- | --- | --- | --- |
| FLUX.2 [pro] (BFL API) | `/v1/flux-2-pro` | up to 8 | `disable_pup` |
| FLUX.2 [max] (BFL API) | `/v1/flux-2-max` | up to 8 | `disable_pup` |
| FLUX.2 [flex] (BFL API) | `/v1/flux-2-flex` | up to 8 | `prompt_upsampling`, `steps` (1–50), `guidance` (1.5–10) |
| FLUX.2 [klein] 9B (BFL API) | `/v1/flux-2-klein-9b` | up to 4 | — |
| FLUX.2 [klein] 4B (BFL API) | `/v1/flux-2-klein-4b` | up to 4 | — |

The five generation nodes output a single `IMAGE`. Two helpers round out the pack:

| Node | Purpose |
| --- | --- |
| Load Image From Path (BFL) | Load an image from any local file path (or the first match of a directory/glob), instead of ComfyUI's input-folder dropdown. |
| FLUX.2 Batch / Sweep (BFL API) | Generate many images in one run — variations of a local image or a whole folder, optionally sweeping one parameter across a range. Picks the model with a widget; outputs a **list** of images and can write each to an output directory. |

They all live under the **strange-pets/BFL/FLUX.2** category.

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

## API keys

**Precedence: `.env` > environment variable > node widget.** The first source with a value
wins, and the console prints which one was used the first time each key is resolved (never
the key itself). If a lower-precedence source is also set, it says so rather than silently
ignoring it.

That order is a deliberate call:

- **`.env` first** because it is the pack's own dedicated, gitignored store. It is the thing
  you edit when you want to change keys, and editing it takes effect immediately — the file
  is re-read whenever its mtime changes, no restart needed. An ambient variable you exported
  months ago should not silently outrank a file you just edited.
- **environment second**, as the standard, machine-wide fallback.
- **widget last**, because it is the insecure option and its value can arrive from a
  *downloaded workflow* rather than from you — you should never unknowingly spend someone
  else's credits because their key was baked into a graph they shared. It still works when
  nothing else is set, so it remains a genuine escape hatch.

### Using a .env

Create `.env` beside this pack (already gitignored), or `~/.strange-pets/.env`, or point
`STRANGE_PETS_ENV` at any file:

```
BFL_API_KEY=bfl-...
STABILITY_API_KEY=sk-...
```

`export` prefixes, quotes, blank lines and `#` comments are all handled. The **API Key
Status** node shows which source each key is coming from and where `.env` was searched for,
without ever printing a key.

### Why not the widget

ComfyUI has no password widget type, so the `api_key` widget renders as **plain visible
text**, is saved into the workflow JSON, and travels inside any PNG that embeds the
workflow. The **Save Images To Directory** node blanks `api_key` before embedding a
workflow, but ComfyUI's own `SaveImage` does not.

On ComfyUI Desktop an environment variable must exist in the app's environment, not just a
terminal: run `setx` (Windows) or set it in your login shell profile, then fully quit and
relaunch — a running instance will not pick it up. A `.env` file avoids all of that.

## Cost, caching, retries and parallelism

**Result cache.** Identical requests reuse a stored image instead of paying again, which
makes iterating on a long chain affordable — tweak a downstream node and the upstream calls
come back instantly, even across restarts. Only *deterministic* requests are cached: BFL
always receives a concrete seed, so a hit needs every input including the seed to match,
and a `-1` seed never hits; Stability treats seed 0 as "pick a random one", so unseeded
requests always go to the API rather than returning their first result forever. Control it
with `STRANGE_PETS_CACHE=0` and `STRANGE_PETS_CACHE_DIR`, and inspect or clear it with the
**Cache Tools** node.

**Retries.** 429 and 5xx responses are retried with exponential backoff, honouring
`Retry-After`. Stability documents its limit as 150 requests per 10 seconds; BFL documents
no 429 at all but can still throttle, so both retry defensively.
`STRANGE_PETS_RETRIES` sets the attempt count (default 3).

**Cost.** BFL returns `cost`, `input_mp` and `output_mp` on every submit — the pack records
them instead of discarding them. The **Cost / Call Report** node totals credits per
endpoint and shows how many calls were served from cache. Stability does not return cost,
so those rows are call counts only.

**Parallelism.** ComfyUI executes nodes one at a time, so *separate* nodes in a graph never
overlap — four generate nodes side by side still run in sequence. Where it is in our hands,
it is parallel: the **Batch / Sweep** node has a `parallel` widget (1–8) that keeps that
many calls in flight at once. The calls are network-bound, so the speedup is close to
linear — a 6-image sweep measured **1.56s serial vs 0.30s at 6 lanes** against a stub with
250ms latency. Results keep their submission order regardless, so a sweep stays aligned
with its parameter values.

## Workflows

`workflows/` holds four ready-made graphs. Drag a `.json` onto the ComfyUI canvas, or use
**Workflow → Open**. Each one carries a note node explaining its own knobs. They all start
pointed at `example.png` — the image ComfyUI ships in its `input/` folder — so swap in your
own.

| File | What it does |
| --- | --- |
| `flux2-style-transfer.json` | Two Load Image nodes into FLUX.2 [pro]: the first keeps its composition, the second lends its look. |
| `flux2-structure-canny.json` | Load Image → `Canny` → FLUX.2 [flex], so a generated scene follows the edges of a source photo. |
| `flux2-variations.json` | One image into four [pro] nodes on fixed seeds 1001–1004, each saving under its own prefix. Four API calls per run. |
| `flux2-batch-sweep.json` | The Batch / Sweep node reading a local file, sweeping `guidance` `2..6` on [flex] with a fixed seed, and writing each result to an output directory. One node, five calls. |

### There is no ControlNet in the FLUX.2 API

`bfl_openapi.json` exposes only the flux-2-* generation endpoints, and their schemas carry
no control fields — no canny endpoint, no depth endpoint, no control strength. The FLUX.2
equivalent is to build the control map locally and hand it over as a reference image, then
say in the prompt what that image is for.

`flux2-structure-canny.json` does exactly that using ComfyUI's core `Canny` node. Its note
covers tuning the thresholds, using `guidance` as the adherence knob, swapping in a depth
preprocessor from `comfyui_controlnet_aux`, and bypassing Canny (Ctrl+B) for plain
image-to-image editing.

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
- **width / height** — `0` (the default) lets the API match the reference image, or choose
  its own size for text-to-image, and is omitted from the request. Any other value is sent
  **verbatim** — the widget no longer snaps to a multiple of 64 or clamps below 64, so
  `1080` stays `1080`. The API is the source of truth and will reject a size it can't honour
  with its own error.
- **safety_tolerance** — `0` is the strictest moderation setting, `5` the most permissive.
- **output_format** — `jpeg`, `png` or `webp`.
- **steps / guidance** — flex only.
- **disable_pup** — pro and max only; the API upsamples prompts by default, so enable this
  to use the prompt exactly as written.

`webhook_url` and `webhook_secret` are not exposed; the nodes poll synchronously.

## Batching, sweeps, and local files

The **FLUX.2 Batch / Sweep** node runs the API in a loop and returns a *list* of images, so
a single downstream `SaveImage` or `PreviewImage` fires once per result.

**Choosing the model** — a `model` widget selects the endpoint (`flux-2-pro`, `-max`,
`-flex`, `-klein-9b`, `-klein-4b`). The model-specific widgets (`disable_pup`,
`prompt_upsampling`, `steps`, `guidance`) are all present; only the ones that belong to the
chosen model are sent, matching each schema.

**Where images come from** (`input_path`):

- a single file → one base image;
- a **directory** or a **glob** (`shots/*.png`) → every matching file becomes its own base;
- **empty** → text-to-image, no base image.

Relative paths resolve under the ComfyUI **input** directory; absolute paths and `~` work
too. Connecting the optional `input_image` socket overrides `input_path` with an upstream
image. The standalone **Load Image From Path** node does the same resolution for the
single-image graphs.

**Where images go** (`output_dir`) — each result is written here (the directory is created
if missing; relative paths resolve under the ComfyUI **output** directory), named with the
model, the swept value and the seed, alongside a `manifest.txt` listing them. Leave it empty
to only pass images downstream.

**Variations vs. sweep:**

- `sweep_param = none`, `variations = 6` → six images. With `seed = -1` each gets a fresh
  random seed; with a fixed seed they're identical (raise `variations` only with `seed = -1`
  or a per-image model).
- `sweep_param = guidance`, `sweep_spec = 2..6:1`, a **fixed** seed → one image per guidance
  value with the seed held constant, so only guidance changes. `variations` multiplies on
  top (N images per swept value).

**`sweep_spec` syntax:**

- explicit list — `3,5,7,9`
- inclusive range — `1.5..8`, or `1.5..8:0.5` with a step
- `steps` and `guidance` are sweepable only on `flux-2-flex`; sweeping a parameter the
  chosen model doesn't have is rejected with a clear message.

**Cost guard** — a run refuses to exceed **100** API calls
(`input files × swept values × variations`); narrow the sweep or lower `variations` past
that. Every image is one paid call, so a sweep bills for the whole grid.

## Utility nodes

Vendor-neutral helpers under **strange-pets/Utils** and **strange-pets/IO**.

| Node | Purpose |
| --- | --- |
| Aspect Ratio → Size | Pick a ratio, get pixel dimensions that respect the model's grid and megapixel budget |
| Fit Image To Endpoint Limits | Rescale to satisfy a chosen endpoint's documented size rules before spending a call |
| Load Images From Directory | Load a whole folder as a list, with glob, sort and slice |
| Save Images To Directory | Save anywhere, with templated filenames and embedded metadata |
| Contact Sheet | Lay a list of images out in a labelled grid |
| Prompt List | One prompt per line becomes a list, so a sweep can vary the prompt |
| Seed List | Explicit, sequential or reproducibly-random seed sets |
| Image Switch | Pick one of six inputs by index — A/B providers without rewiring |
| Join Image + Alpha | Attach a mask as alpha, for the endpoints that mask via transparency |
| API Key Status | Which source each key comes from, without revealing it |
| Cost / Call Report | Credits and call counts for the session |
| Cache Tools | Inspect or clear the result cache |

### Fit Image To Endpoint Limits

Each endpoint has its own hard rules — `upscale/fast` wants 32–1536 per side and at most
1,048,576 pixels, most edit endpoints want ≥64 per side and at most 9,437,184. Feeding a
2048px image to `upscale/fast` is a guaranteed 422, i.e. a paid round-trip to learn
something a node could have fixed. Stability's numbers are **parsed out of the shipped
OpenAPI spec at import time**, so they cannot drift from the API; the FLUX.2 rules come
from BFL's published limits. `shrink only` never upscales, and says so in the summary if
the image is still under a minimum.

### Contact Sheet

Takes the *list* the Batch / Sweep node emits (it sets `INPUT_IS_LIST`, so it sees the
whole set at once) and builds one labelled grid. Wire the sweep's labels in and the swept
value is printed under each tile — a guidance sweep becomes one image to judge instead of
five files to open.

### Aspect Ratio → Size

Outputs `width`, `height`, an `aspect_ratio` string and a human-readable `summary`. Wire
`width`/`height` into the FLUX.2 nodes and `aspect_ratio` into the Stability ones, so a
single node drives both APIs.

Sizes come from BFL's published limits for FLUX.2: **minimum 256px a side, up to 4MP,
recommended at or below 2MP, and dimensions snapped to a multiple of 32**. That last rule
is why a hand-typed `1080` becomes `1088` — *the API itself* rounds to /32, whatever the
node sends. Letting this node pick the size removes the surprise.

Both sides are snapped to the grid together and the best pairing is chosen, rather than
trimming one side to fit the budget — trimming skews the ratio badly at a /32 grid (16:10
drifts to 1.57:1). Every supported ratio lands within ~1% of true:

| Ratio | @1MP | @2MP | @4MP |
| --- | --- | --- | --- |
| 1:1 | 992×992 | 1408×1408 | 1984×1984 |
| 16:9 | 1312×736 | 1824×1024 | 2624×1472 |
| 21:9 | 1504×640 | 2144×928 | 3040×1312 |
| 3:2 | 1216×800 | 1728×1152 | 2400×1600 |
| 4:5 | 864×1088 | 1248×1568 | 1760×2208 |

`target` picks the rule set (`flux-2`, `stability`, or `custom`), `megapixels` sets the
budget and is clamped to the target's ceiling, `multiple_of` overrides the grid (0 uses the
target's own), and `orientation` flips a ratio to landscape or portrait without needing a
second entry in the list. `custom` accepts freeform ratios — `16:10`, `1.85:1`, `4x5` — and
still reports the nearest Stability enum, since Stability only accepts its nine.

### Load Images From Directory

`directory` (absolute, or relative to ComfyUI's input folder), a `pattern` glob (`*.png`,
`shot_*.jpg`), `sort_by` name/modified/size with `descending`, plus `start_index` and
`limit` to take a slice. Outputs a **list** of images and a matching list of filename stems,
so everything downstream runs once per image. Images of different sizes are fine.

### Save Images To Directory

Writes anywhere — `output_dir` is absolute or relative to ComfyUI's output folder, created
if missing.

**Filenames** come from `filename_template`, with these tokens:

```
{prefix} {index} {date} {time} {datetime} {seed} {model} {width} {height} {prompt} {ext}
```

Format specs work, so `{prefix}_{index:04d}` gives `strange-pets_0001`. `{prompt}` is
slugified and truncated. The index continues from whatever is already in the directory, so
saving a list of images numbers them consecutively. Unless `overwrite` is on, an existing
name gets a numeric suffix instead of being replaced. A bad token names itself in the error
rather than failing obscurely.

**Metadata.** With `embed_metadata` on, PNGs get a `parameters` text chunk (the prompt plus
model/seed/size, the convention most viewers read) and a `strange_pets` chunk holding the
same values as JSON. `save_sidecar_json` writes `<name>.json` next to the image and works
for every format, unlike text chunks — use it for jpeg and webp.

`embed_workflow` additionally embeds the ComfyUI graph so the image reopens as a workflow.
**It is off by default, and any `api_key` widget is blanked before embedding.** Widget
values are stored positionally, so the node recovers each `api_key`'s index from the node
class's own `INPUT_TYPES` — including the `control_after_generate` slot the frontend
inserts after a seed — and blanks exactly that entry, in both the UI workflow and the
API-format prompt. This matters: without it, a key typed into an `api_key` widget would
travel inside every PNG you share.

## Stability AI nodes (Stable Image v2beta)

17 nodes wrapping `api.stability.ai`, under **strange-pets/StabAI/**. Every node outputs a
ComfyUI `IMAGE`, so they chain freely into each other and into the FLUX.2 nodes
(generate → upscale → edit).

| Node | Endpoint | Mode | Notes |
| --- | --- | --- | --- |
| Stability Generate Ultra | `generate/ultra` | sync | Text-to-image; connect `image` for image-to-image (then `strength` is sent) |
| Stability Generate Core | `generate/core` | sync | Fast/affordable text-to-image, no image input |
| Stability Generate SD3.5 | `generate/sd3` | sync | `mode` picks t2i vs i2i; `model` picks large / large-turbo / medium |
| Stability Erase | `edit/erase` | sync | Mask or the image's alpha channel |
| Stability Inpaint | `edit/inpaint` | sync | Mask or alpha; `grow_mask` up to 100 |
| Stability Outpaint | `edit/outpaint` | sync | At least one of left/right/up/down must be non-zero |
| Stability Search & Replace | `edit/search-and-replace` | sync | No mask — segments from `search_prompt` |
| Stability Search & Recolor | `edit/search-and-recolor` | sync | Segments from `select_prompt` |
| Stability Remove Background | `edit/remove-background` | sync | png/webp only, so transparency survives |
| Stability Replace BG & Relight | `edit/replace-background-and-relight` | **async** | Up to 3 image inputs; polls until ready |
| Stability Upscale Fast | `upscale/fast` | sync | ~1 second, no prompt |
| Stability Upscale Conservative | `upscale/conservative` | sync | Stays close to the original, up to ~4MP |
| Stability Upscale Creative | `upscale/creative` | **async** | Reimagines degraded input; polls until ready |
| Stability Control Sketch | `control/sketch` | sync | `control_strength` 0–1 |
| Stability Control Structure | `control/structure` | sync | Same fields, structural reference |
| Stability Control Style | `control/style` | sync | `fidelity` 0–1 |
| Stability Style Transfer | `control/style-transfer` | sync | Needs both `init_image` and `style_image` |

The two async nodes POST, receive `{"id": ...}`, then poll `GET /v2beta/results/{id}`
(202 = still running, 200 = done) and block until the image is ready. Results stay
fetchable for 24 hours.

### Masks

`erase` and `inpaint` take an optional `MASK` input. **White = act on this pixel, black =
leave it alone** — ComfyUI's mask polarity already matches Stability's, so nothing is
inverted on the way out. With no mask connected, the API falls back to the alpha channel of
`image`; a connected mask takes precedence.

### Empty fields

`negative_prompt` and the optional prompts are omitted from the request when left blank,
and `style_preset` / `light_source_direction` are omitted when set to `none` — so a blank
widget never sends an empty string the API would reject.

### Moderation

Stability's Stable Image API exposes **no** safety, moderation, or permissiveness
parameter — there is nothing equivalent to BFL's `safety_tolerance` to turn down. I checked
every one of the 17 request schemas. Moderation surfaces in exactly two ways:

- **HTTP 403 `content_moderation`** — the request was refused before generating.
- **HTTP 200 with `finish-reason: CONTENT_FILTERED`** — an image *was* generated but
  violated the policy, so the API returns it **blurred**.

In the second case the node inspects the response header and closes the connection without
reading the body, so the blurred image is never downloaded, and raises a moderation error
naming what happened. Both cases say plainly that no permissiveness setting exists, so the
prompt or input image is the only thing you can change.

### Errors

Stability's error body is `{id, name, errors[]}` — `errors` is an array of human-readable
strings (there is no `message` field). The nodes join those and surface them verbatim, so a
422 reads like `prompt: is required; seed: too big`, not a generic ComfyUI traceback.

### Client-side validation

These rules are checked before spending a call, rather than learned from a paid round-trip:
SD3 in `image-to-image` mode without a connected image; outpaint with all four sides zero;
style-transfer missing `init_image` or `style_image`; relight missing `subject_image`.

## FLUX.2 errors

Validation failures (422) are surfaced with the API's own `detail` messages, field by
field. The two moderation states are reported distinctly — `Request Moderated` when the
prompt is refused before generation, `Content Moderated` when the finished image is
blocked — rather than as a generic failure. Polling gives up after 10 minutes and honours
ComfyUI's cancel button.
