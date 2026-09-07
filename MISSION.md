Build a ComfyUI custom node package that wraps the Black Forest Labs FLUX.2 API 
for local use in ComfyUI Desktop.

Reference spec: I've saved the official OpenAPI spec at ./bfl_openapi.json 
(fetched directly from https://api.bfl.ai/openapi.json). Treat this as the 
source of truth for every endpoint path, param name, type, min/max, and default 
— do not guess or rely on prior knowledge of the API, it has changed recently.

Scope — nodes for these endpoints (schemas named in the OpenAPI file):
- flux-2-pro, flux-2-max (schema: Flux2Inputs) — text-to-image + editing, 
  up to 8 reference images, NO steps/guidance (zero-knob tier).
- flux-2-flex (schema: Flux2FlexInputs) — same but exposes steps (1-50) and 
  guidance (1.5-10).
- flux-2-klein-9b, flux-2-klein-4b (schema: Flux2KleinInputs) — up to 4 
  reference images only.
One ComfyUI node per endpoint (5 nodes total), sharing a common base class 
for the HTTP/polling logic.

Dynamic image inputs (don't hardcode a fixed number of optional sockets):
- Each node starts with exactly one optional IMAGE input socket 
  (input_image).
- Ship a small frontend extension (web/js file loaded via WEB_DIRECTORY) 
  that hooks into node creation and, whenever the last image input socket 
  gets connected, appends a new optional socket (input_image_2, _3, etc.), 
  up to that node's max (8 for pro/max/flex, 4 for klein). If a socket is 
  disconnected and it's the last one, remove trailing empty sockets back 
  down to one. Look at how ComfyUI-VideoHelperSuite or Impact-Pack implement 
  their dynamic/growable inputs for the established pattern — follow that 
  convention rather than inventing a new one.
- On the Python side, execute() should accept **kwargs for the image slots 
  actually connected and only include the ones that are non-None in the 
  request body, keyed to the exact field names in the OpenAPI schema 
  (input_image, input_image_2, ...).

Every other param from each schema becomes a widget:
- prompt: multiline STRING
- width/height: INT (respect each schema's minimum; 0/default means "match 
  input image" per the docs — surface that in the widget tooltip)
- seed: INT, -1 = random seed client-side
- safety_tolerance: INT slider within that schema's min/max
- output_format: COMBO (jpeg/png/webp per OutputFormat enum)
- steps/guidance: only on the flex node, per its min/max
- disable_pup (pro/max) vs prompt_upsampling (flex/klein): match whichever 
  field the schema actually defines — don't assume they're the same field
- webhook_url/webhook_secret: skip these, not needed for local sync use

API key: read from BFL_API_KEY environment variable, with a fallback STRING 
widget (password-style / masked if ComfyUI widget types support it).

Async flow: POST to create the task, then poll polling_url with a short 
sleep loop (blocking inside execute() is fine — standard pattern for API 
node packs). Handle every value in the StatusResponse enum distinctly 
(Pending, Reasoning, Generating, Request Moderated, Content Moderated, 
Ready, Error) — surface moderation as a specific error, not a generic 
failure. Signed result URLs expire in 10 minutes; download immediately on 
Ready.

Image conversion: ComfyUI IMAGE tensor <-> base64 PNG for inputs; downloaded 
result URL -> ComfyUI IMAGE tensor for output, so results can feed into 
SaveImage, PreviewImage, or back into another one of these nodes as the 
next edit's input_image.

Package layout: standard custom_nodes structure — __init__.py with 
NODE_CLASS_MAPPINGS/NODE_DISPLAY_NAME_MAPPINGS, WEB_DIRECTORY pointing at 
the js extension, requirements.txt (requests, torch, numpy, pillow), and a 
README covering install + BFL_API_KEY setup.

Surface API errors (validation 422s, moderation, etc.) with the actual 
message from the response body rather than a generic ComfyUI exception.
