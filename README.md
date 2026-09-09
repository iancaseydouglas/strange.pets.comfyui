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
- **Tarot** — a canonical 78-card manifest, a slot loader that maps a folder of art onto it,
  per-card prompt assembly, colour palettes, and card frame/lettering compositing. 6 nodes.
- **Style cascade** — deck constraints, a style lock, scoped style layers over any subset, the
  resolver that collapses them per card, and a cohesion report. 5 nodes.

Everything is generated against the official OpenAPI descriptions shipped alongside the
code (`bfl_openapi.json` and `stability-openapi.json`) — endpoint paths, field names,
ranges and defaults all come from those documents rather than from memory.

All 47 nodes are categorised under **strange-pets/** in the node browser, sub-grouped by
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

### Encrypted .env files (dotenvx)

A `.env` encrypted with [dotenvx](https://dotenvx.com) works as-is. `dotenvx encrypt` leaves
the file a perfectly ordinary `.env` — same `NAME=value` lines, same comments — but each
value becomes `encrypted:BPhOsB…`, and the private key moves into a `.env.keys` beside it
(gitignored here) or into `DOTENV_PRIVATE_KEY`. The point is that the `.env` itself becomes
safe to commit and to sync between machines.

```bash
dotenvx set BFL_API_KEY bfl-...      # writes an encrypted value
dotenvx encrypt                       # or encrypt a .env you already have
```

Nothing else changes. Encrypted values are decrypted at read time and the rest of the pack
never sees a ciphertext, so **precedence, the mtime re-read, and every node stay exactly the
same** whether the file is plaintext or encrypted. Mixed files are fine — encrypt the keys,
leave `STRANGE_PETS_CACHE_DIR` in the clear.

Decryption shells out to the `dotenvx` binary, which is only looked for when a value actually
needs it:

- `STRANGE_PETS_DOTENVX=0` turns the whole thing off. Encrypted values are then dropped
  rather than passed on as keys, and the reason is logged.
- `STRANGE_PETS_DOTENVX_BIN` points at the executable. Worth setting on ComfyUI Desktop,
  which inherits the desktop session's `PATH` rather than a login shell's — a Homebrew or
  `~/.local` install can be invisible to it. `/usr/local/bin`, `/opt/homebrew/bin` and
  `~/.local/bin` are checked as fallbacks anyway.

Two details are handled rather than inherited. dotenvx merges the process environment over
the file and lets the environment win; this pack's documented order is the opposite, so the
names being decrypted are stripped from the child process's environment and the file's own
value is what comes back. And dotenvx **exits 0 and echoes the ciphertext back** when it
cannot decrypt — which would otherwise send `encrypted:BPhOsB…` to the API as your key — so
anything still ciphertext after the call is dropped and reported instead.

The **API Key Status** node prints all of it: which source each key came from, where `.env`
was searched for, and what happened to any encrypted values.

The alternative, if you would rather not have the pack shell out at all, is to launch ComfyUI
under `dotenvx run -- python main.py`, which puts the decrypted values in the environment
before the pack ever starts. That works today with no configuration.

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

`workflows/` holds eleven ready-made graphs. Drag a `.json` onto the ComfyUI canvas, or use
**Workflow → Open**. Each one carries a note node explaining its own knobs. The four general
graphs start pointed at `example.png` — the image ComfyUI ships in its `input/` folder — so
swap in your own; the five deck graphs point at `tarot/…` paths under the input and output
directories.

**General:**

| File | What it does |
| --- | --- |
| `flux2-style-transfer.json` | Two Load Image nodes into FLUX.2 [pro]: the first keeps its composition, the second lends its look. |
| `flux2-structure-canny.json` | Load Image → `Canny` → FLUX.2 [flex], so a generated scene follows the edges of a source photo. |
| `flux2-variations.json` | One image into four [pro] nodes on fixed seeds 1001–1004, each saving under its own prefix. Four API calls per run. |
| `flux2-batch-sweep.json` | The Batch / Sweep node reading a local file, sweeping `guidance` `2..6` on [flex] with a fixed seed, and writing each result to an output directory. One node, five calls. |

**Stability control:**

| File | What it does |
| --- | --- |
| `stability-control-routing.json` | The four ControlNet-shaped Stability endpoints — `control/sketch`, `control/structure`, `control/style`, `control/style-transfer` — wired so one image can go through any combination of them by changing four integers. Every stage sits behind an Image Switch that can read the source, a local Canny line map, or any earlier stage's output. All switches on `1` runs the four in parallel off the source (compare mode); a cascade of `1,3,4,5` makes them a serial chain. |

**Deck work** — these use the Tarot nodes and are built around a set of cards rather than one
image:

| File | What it does |
| --- | --- |
| `tarot-style-transfer.json` | A folder of cards through FLUX.2 [pro] with one style reference on the second image slot, each keeping its own composition and saving under its own filename. A Stability Style Transfer lane runs the same pair with explicit `style_strength` / `composition_fidelity` / `change_strength` dials. |
| `tarot-structure-fidelity.json` | The same structure held three ways, so you can see what each dial costs: Canny thresholds decide how much structure exists, `guidance` decides how hard it is followed (swept 2→8 onto a contact sheet), and Stability's `control_strength` does it without an edge map at all. |
| `tarot-variation-sweep.json` | Two sweeps off one image on the same fixed seed — `guidance` and `steps` — each landing on its own labelled contact sheet, with the cost report beside them. |
| `tarot-generate-and-edit.json` | A chain you sit inside: generate, then edit, then edit again, each stage taking the last result back in. Fixed seeds make every upstream stage a cache hit, so revising stage three costs only stage three. |
| `tarot-deck-pipeline.json` | The whole thing. Manifest → slot loader → per-card prompts → generate → frame → lettering → save, plus a contact sheet of the finished deck. |
| `tarot-deck-derive.json` | A deck derived from a deck: the full style cascade — invariants, a locked style, scoped layers over subsets, the fidelity vector — resolved per card, generated, assembled, saved and measured. Starts on the 12-card proof set. |

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
a single downstream `SaveImage` or `PreviewImage` fires once per result. It also returns a
matching list of `labels` naming what varied on each one, for the Contact Sheet to caption.

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
whole set at once) and builds one labelled grid. Wire the sweep's `labels` output in and the
swept value is printed under each tile — a guidance sweep becomes one image to judge instead
of five files to open. The label names only what varied (`guidance 3.5  seed 4242`), while the
filename on disk carries the full stem.

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
{prefix} {label} {index} {date} {time} {datetime} {seed} {model} {width} {height} {prompt} {ext}
```

Format specs work, so `{prefix}_{index:04d}` gives `strange-pets_0001`. `{prompt}` is
slugified and truncated. The index continues from whatever is already in the directory, so
saving a list of images numbers them consecutively.

`{label}` and `{index}` both take optional inputs, which is what makes a deck save legibly.
Wire a card's slug into `label` and its slot number into `index` and
`{index:02d}_{label}` gives `00_the-fool.png`, `01_the-magician.png` — named by card and
ordered by position, rather than by the order the run happened to write them. Connected,
`index` replaces the running counter entirely. Unless `overwrite` is on, an existing
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

## Tarot nodes

Six nodes under **strange-pets/Tarot** that treat a deck as a structure rather than a pile of
images: a set of positions, each with a name and a number, that art gets mapped onto. They are
vendor-neutral — the generate step in the middle can be any node in this pack, or none.

| Node | Purpose |
| --- | --- |
| Tarot Deck Manifest | The deck's slots in order, each with its title, numeral, suit, rank and element |
| Tarot Palette | A colour reference as hex text and as a swatch image, written or extracted from a card |
| Tarot Deck Slot Loader | Maps a folder of art onto those slots by filename, and reports what did not land |
| Tarot Prompt Builder | One prompt per card from a template plus a shared style block |
| Tarot Card Frame | Trim size, DPI, bleed, margins and a border — the card as a printable object |
| Tarot Card Lettering | The name and numeral, with real letter-spacing |

`workflows/tarot-deck-pipeline.json` wires all five together and carries a long note.

### Tarot Deck Manifest

`tradition` picks `rws`, `marseille`, `thoth` or `golden-dawn` as a starting point, setting
the major arcana names and the suit and court names together. Golden Dawn seats the Knight at
the top of the court in place of a King (Princess, Prince, Queen, Knight — Crowley carried that
into the Thoth) and ends the majors on The Universe. Book T titles vary between recensions;
`The Foolish Man`, `The Blasted Tower` and `The Last Judgement` are the forms shipped, and if
your source reads otherwise the definition file below is where you fix it.

**Order is nomenclature.** A card's number is its position in the majors list and nothing
else, so the split between the Golden Dawn line — Strength VIII, Justice XI, which Waite
carried into the RWS — and the Marseille line that seats them the other way is a difference in
the *order of that list*. `rws` and `marseille` differ in ordering as well as in language, and
swapping two entries renumbers those cards, numerals included.

`scope` narrows the run — `major arcana`, `pips only`, one suit at a time — which matters
mostly as a cost control, since every card in scope is an API call downstream.

### Selectors

`scope = selector` takes an expression, and the same language scopes styles and colour rules
later on. **Space is AND, a comma is OR, `!` negates one term.**

| Selector | Resolves to |
| --- | --- |
| `courts wands` | the four Wands courts |
| `courts !wands` | the twelve courts of the other suits |
| `pips 7` | the four Sevens |
| `number=7` | the four Sevens **and The Chariot**, which genuinely is card seven |
| `queen`, `fire`, `aces`, `majors` | by rank, element, group |
| `majors, aces` | twenty-six cards |
| `index=0-5`, `swords 2-4`, `number=2-5` | spans |
| `the-tower, the-devil` | named cards |

Terms are groups (`all`, `majors`, `minors`, `courts`, `pips`, `aces`), suit names, `suit1`…`suitN`
by position, rank names, elements, card slugs, and numbers. The positional `suitN` forms keep a
selector working after you rename Wands to Lanterns.

An unrecognised term **raises**, naming what it would accept, rather than matching nothing —
because a silent no-match is the expensive failure. A mistyped suit means that suit quietly
keeps the deck's base style and you find out after paying for the run.

### The proof set

`scope = proof set` is the smallest spread that exercises every dimension the scoping can
address — twelve cards instead of seventy-eight, for proving a pipeline before committing to a
full run:

```
index=0, index=16, index=18, courts suit1, pips 7, suit2 13
```

| Cards | What they prove |
| --- | --- |
| The Fool, The Tower, The Moon | majors; numeral `0` and a late roman; a card carrying a hard colour rule |
| Page/Knight/Queen/King of Wands | a **complete court in one suit** — the cohesion case |
| Queen of Cups | a court in *another* suit, so a `courts` rule and a `courts wands` rule are visibly different |
| Seven of Wands / Cups / Swords / Pentacles | one number across all four suits, and all four elements |

It is written positionally, so it selects the same twelve positions under every tradition and
under a definition of your own — only the names change.

### Deck definitions

Every name and every position lives in one JSON object, and `definition_path` loads one:

```json
{
  "name": "Tarot of the Hidden Light",
  "majors": ["The Unlit Lamp", "The Magician", "…"],
  "suits": [{"name": "Lanterns", "element": "Fire"},
            {"name": "Vessels",  "element": "Water"}],
  "pips": ["Ace", "Two", "…", "Ten"],
  "courts": ["Seeker", "Rider", "Mother", "Elder"],
  "major_element": "Spirit"
}
```

Nothing here is fixed at 78. The list lengths *are* the deck — a fifth suit, three pips, two
courts and two majors builds a 27-card deck and every downstream node follows, because the
selectors, the slot loader and the lettering all read the deck rather than assume it. Per-suit
`element` is explicit rather than positional, so a deck that attributes Swords to Fire and
Wands to Air says so instead of fighting the code.

You do not have to write one by hand. `save_definition_to` writes the **resolved** definition
— tradition plus every override on the node — so the way in is: start from a tradition, adjust
`suit_names` / `court_names` / `custom_titles`, save, then load it back through
`definition_path` as your deck's own nomenclature. What comes back out is what ran.

The quick overrides still work without a file: `suit_names` and `court_names` take one
comma-separated name per suit and per court rank.

### Renaming cards

`rename` takes one `target = new title` per line, and the target is whatever you have to
hand — a slug, an index, a code, or the card's current title:

```
temperance         = Art
21                 = The Aeon
major-16           = The Lightning House
The High Priestess = The Veiled One
king-of-wands      = Elder of Lanterns
```

**A rename does not break what you have already written about that card.** `slug` follows
the new name, so filenames and lettering read correctly, but `base_slug` keeps the original
— so a selector or a colour rule written against `temperance` still finds the card after it
becomes Art, and so does a file still called `temperance_v4.png`. The alternative is that
renaming one card silently orphans every rule mentioning it, which is a bad trade for
convenience.

Both names work in both directions: `art_final.png` and `temperance_v4.png` land on the same
slot, and `temperance` and `art` both select it.

Renames survive `save_definition_to`. Majors are stored as their titles; a **minor** rename
has nowhere to live in a suit × rank grid, so it is written to a `renames` block keyed by
code and applied when the definition loads:

```json
"renames": { "wands-14": "Elder of Lanterns" }
```

An unknown rename target raises and says what it would accept, rather than being ignored.

`numerals` gives roman, arabic or none. Courts have no numeral in any setting.

### Tarot Deck Slot Loader

Files are matched to slots **by name, not by order**, so a folder that grew organically still
lands correctly. All of these resolve:

```
the-fool.png   03-empress.png   HighPriestess_final.png   XVI_the_tower.png
wands_07.png   SevenOfCups_v2.png   queen-of-swords.png   pentacles-14.png
```

Each card generates a set of spellings — slug, title, article-stripped title, the name it
had before you renamed it, number plus name, roman numeral, `suit07`, `07suit`,
`sevenofwands`, `wandsseven` — and the **longest** matching one wins, so `seven-of-wands`
beats the bare suit name.

Match strictness scales with how much a name has to say. Four characters and up may sit
inside a longer stem, which is what makes `SevenOfCups_v2.png` work. A three-letter name
(`art`, `sun`) must land on a **whole word**, so `sun_v2.png` finds The Sun and
`sunset_moodboard.png` does not. A purely numeric alias must match exactly, or a bare `07`
would claim any file with a 7 in it. Anything the matcher still gets wrong is fixed with one
`slug = filename` line in `overrides`.

The `report` output is the point of the node as much as the images are. It names every file
it matched and to what, every file it could not place, and every slot still empty — which is
much cheaper to read before a run than to discover after one.

`on_missing` decides what an unfilled slot does:

- **blank slot** (default) keeps the deck's shape while it is unfinished. The position stays
  in the run holding a labelled placeholder, so a contact sheet at the end shows the whole
  deck *with its gaps*, rather than a shorter deck that hides them.
- **skip** drops it, and spends nothing on it.
- **error** refuses to run until the deck is complete.

Outputs are parallel lists — `images`, `titles`, `numerals`, `slugs`, `indices` — plus the
filtered `deck`. They stay aligned with each other under `skip`, which is why the lettering
node can be fed `titles` and `numerals` directly without them drifting apart.

### Tarot Palette

Colour as both text and image. Hex codes are read out of any surrounding prose, so a colour
rule stays one sentence — `dominant red, burning — #B3121B, #7A0E14` — instead of being split
across a prose field and a colour field. Both `#rgb` and `#rrggbb` are read and normalised.

`extract_count` with an image connected pulls that many dominant colours off it, which is how
you lock a palette **from a card you have already approved** rather than inventing one.

The `swatch` output is the point. A model follows a rendered swatch far more reliably than it
follows six hex codes in a prompt, so the swatch goes into a reference image slot and the codes
go into the prompt as well — belt and braces. The same swatch is what you check the result
against afterwards. `labels` prints each code on its block: useful to look at, noise to a
model, so leave it off for a swatch you are going to send.

## The style cascade

Five nodes that answer one question: how do you make seventy-eight separate API calls come
back looking like one deck?

**Deck Constraints** · **Style Lock** · **Style Scope** (chainable) · **Style Resolve** ·
**Deck Cohesion Report**

`workflows/tarot-deck-derive.json` wires them all and carries the long note.

### Why a cascade

The drift is not in the style prompt — all 78 calls already share that. It is that a model
reinterprets one style block differently depending on what the base card looks like: a dense
dark card and an airy one pull the same words apart. The only lever strong enough to counter
that is a **visual** reference already in the deck's own idiom. So the lock is an approved
card, or several, and everything else is scoping and bookkeeping around it.

Each **Style Scope** adds a layer over any subset (the selector language above), and **later
wins** — the rightmost node that matches a card is the specific one. Chain order is the whole
rule; there is no hidden specificity calculation, because chain order is something you can see
on the canvas and a computed specificity is something you would have to reason about.

Layers combine differently by kind, deliberately:

- **Text accumulates.** A Wands court gets the deck's prose, then the courts', then its own.
- **Anchors do not.** The most specific matching set leads the plate and the deck's own
  anchors backfill the remaining tiles, so a suit's courts read like each other first and like
  the deck second.
- **Fidelity overrides.** An axis left at `-1` inherits, so a scope can change structure alone.

### Anchors

`anchor_dir` is a folder of approved cards. Use **several deliberately unalike ones** — a
major, a pip, a court. A single anchor is the failure mode: the deck starts inheriting its
*composition* rather than its style. They composite into one plate occupying one reference
slot, and a model shown several cards at once is likelier to read the style they share than to
copy the one picture it was handed.

### The fidelity vector

Five axes, each `1.0` holding the seed card's version and `0.0` letting it change entirely:
`structure`, `style`, `colour`, `lighting`, `iconography`. The named operations are points in
that space, so any combination is just another point:

| | structure | style | colour | iconography |
| --- | --- | --- | --- | --- |
| restyle | hold | free | free | hold |
| restructure | free | hold | hold | hold |
| re-symbolize | hold | hold | hold | **free** |
| retone | hold | hold | **free** | hold |

**How it reaches the model, honestly.** Stability has real parameters for this, and Resolve
emits them per card — `control_strength`, `composition_fidelity`, `style_strength`,
`change_strength`. **The FLUX.2 API has none**: its schemas carry no control strength and no
style weight, only `guidance` on flex. Over FLUX.2 the vector therefore reaches the model as
*prompt language plus which image sits in which reference slot*. That is reproducible and it
composes, but it is soft where a local IPAdapter — which works at cross-attention, on weights
this API does not expose — would be hard.

The wording lives in **`phrases.json`** beside the pack, as five bands per axis. Edit it: the
exact phrasing is the part worth tuning, and tuning it should not mean editing Python.
`phrases_path` points at your own copy.

### Constraints belong to the deck

"The Tower is red no matter what" is a fact about *your deck*, not about a style — so it sits
beside the manifest, not inside the lock, and travels through every derivation. Swap the whole
style and the constraints still land, appended last after all style text. Same selector
grammar, so they scope as freely:

```
the-tower : dominant red, burning — #B3121B, #7A0E14
the-moon  : cold silver-blue, no warm tones
wands     : warm — amber, ember, brass
```

Hex codes are read out and rendered into that card's plate as a swatch tile.

### Read the report

Resolve prints which layers each card ended up under, and flags cards that only ever matched
the base layer:

```
The Tower                <- deck | majors  [the-tower]
Queen of Wands           <- deck | courts suit1  [wands]
Seven of Cups            <- deck

only the base layer (5): Seven of Wands, Seven of Cups, …
```

With several overlapping scopes across 78 cards you cannot hold that in your head, and a card
that fell through every scope is exactly the one that will break cohesion.

### Deck Cohesion Report

Measures the finished deck: lightness and saturation against the deck's **own** median, and
whether any colour constraint that named hex codes was actually honoured. It costs no API
calls, so run it every time — at 78 cards drift is not something you can see by eye.

```
  ! The Moon   lighter (0.92 vs 0.20, +71.8 dev); flatter (0.05 vs 0.54, -49.3 dev)
  colour constraints: 1 card(s) checked, 1 adrift
  ! The Tower (#B3121B, off by 0.76)
```

### Tarot Prompt Builder

`{title} {numeral} {arcana} {suit} {rank} {element} {slug} {index} {style}`

The split between `template` and `style` is deliberate: the template is the sentence that
stays fixed across a deck, `style` is the one block retuned between whole-deck runs. Change
`style` and all 78 cards change together, consistently.

### Tarot Card Frame

Trim size at a real DPI — `tarot 2.75x4.75in` is the standard 70×120mm stock, alongside
poker, bridge, large and square, or `custom` in pixels. `bleed_mm` adds print bleed outside
the trim; 3mm is the usual ask, 0 gives a screen-size card.

`border` is one switch for the whole frame. Off collapses the margins to nothing, draws no
rule, and skips the overlay and the art-box corner radius — full-bleed art at the same trim
size. Trim, dpi, bleed and the card's own corner radius are card geometry and still apply, so
the two states are directly comparable. It is a widget rather than a Ctrl+B bypass because
bypassing the node loses the card geometry as well as the border; and because framing costs
nothing once the art exists, two frame nodes off the same generate node will give you the
deck both ways for the price of one run.

`margin_pct` is the border band as a percentage of card *width*. `top_margin_extra_pct` and
`bottom_margin_extra_pct` open up the head and foot for a numeral and a title — the art box
shrinks to make room, so lettering never sits on the illustration.

`border_style` is `none`, `keyline`, `solid band`, `double rule` or `inset panel`. Or leave it
`none` and put a border designed elsewhere into `overlay_path` — any PNG with alpha, laid over
the finished card at card size. `frame_overlay` + `frame_alpha` does the same from upstream in
the graph, for a frame the API generated.

### Tarot Card Lettering

`title` and `numeral` are widgets you can also wire, so a deck letters itself.

`tracking_px` is real letter-spacing. Pillow has no tracking parameter and display lettering
on a card almost always wants some, so the text is laid out a glyph at a time rather than
handed over whole. Sizes are percentages of card height, so they survive a change of trim
size.

**Set `font_path`** to a `.ttf` or `.otf`. Blank falls back to Pillow's built-in face, which
is fine for placing the block and not fine for print.

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

### Routing the control endpoints

`workflows/stability-control-routing.json` exists because these four are the only place in
the pack where structural adherence is **a number you set** rather than a sentence you phrase
— the FLUX.2 API exposes no control parameters at all. It is the graph to use when you want
to feel out what a fidelity dial actually does.

Two things it is built to stop you tripping over:

**`control/style` keeps no structure.** It reads the image as a *style* reference and
generates a new picture from the prompt at whatever `aspect_ratio` you set. The other three
take structure from the image; this one does not, and it is the one people wire up expecting
composition to survive.

**Order is fixed by the wiring, and that is a constraint rather than an oversight.** A ComfyUI
graph must be acyclic, so a switch can only read stages above it — wiring Structure to read
Style's output while Style reads Structure's is a cycle and the graph refuses to run. The
switches give you every *combination* and every sub-sequence in that order; they cannot give
you every permutation. Drag the connections for a different order.

Every un-bypassed stage is a paid call whether or not you use its result, so a default run is
four. **Ctrl+B** bypasses a stage — the input passes straight through to the output, so
downstream switches receive whatever went in and the stage costs nothing.

The seeds are set to **7 rather than 0** on purpose: Stability treats seed 0 as "pick a random
one", which makes two runs incomparable and means nothing is ever served from cache. Fixed
seeds make the routing the only variable, and make re-runs free.

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
