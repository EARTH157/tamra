# External local models: use GGUF files from Ollama, LM Studio and Hugging Face in place

Status: approved design, not yet implemented. Date: 2026-10-23.

## Goal

Tamra should not have to own a copy of every chat model. When the user already has a GGUF
model from Ollama, LM Studio or the Hugging Face cache, Tamra finds it and runs it from where
it is. Importing a model file remembers its path instead of copying it.

Out of scope (YAGNI): user-added scan folders (Import covers them), hashing LM Studio
files, downloading into another tool's store, and any change to the embedding or OCR
models. Those stay Tamra-owned, copied, and pinned by sha256.

## 1. Discovery and model identity

### `tamra/models/external.py`

The module only reads. It never writes, moves or deletes another tool's files.

```python
@dataclass(frozen=True)
class ExternalModel:
    source: str          # "ollama" | "lmstudio" | "huggingface"
    name: str            # "qwen3:8b", "Qwen3-8B-Q4_K_M"
    path: Path           # the GGUF file itself
    sha256: str | None   # only when the store names the file by its hash

def scan(home: Path, env: Mapping[str, str]) -> list[ExternalModel]
```

`home` and `env` are parameters so tests can build fake stores under `tmp_path`.

How each source is read:

- **Ollama.** The root is `env["OLLAMA_MODELS"]`, or `home/.ollama/models` when that is
  unset.
  - Every file under `manifests/` is parsed as JSON. The layer with media type
    `application/vnd.ollama.image.model` has a digest `sha256:<hex>`. That digest names
    the blob `blobs/sha256-<hex>`, and the blob must start with the bytes `GGUF`. The
    model's `sha256` is `<hex>`.
  - A manifest that also has an `application/vnd.ollama.image.projector` layer is
    skipped (a vision model).
  - The name is `<model>:<tag>`, taken from the manifest's last two path parts.
  - Several tags can share one blob. That blob is listed once, under a tag other than
    `latest` when the blob has one.
- **LM Studio.** Tamra looks for `*.gguf` files at most three levels deep under:
  - `home/.lmstudio/models`
  - `home/.cache/lm-studio/models`
  - `<dir>/models`, where `<dir>` is the path in `home/.lmstudio-home-pointer` when that
    file exists.

  The name is the file stem and `sha256` is `None`. These locations come from community
  sources, so check them against a real install.
- **Hugging Face cache.** The root is `env["HF_HUB_CACHE"]`, then `env["HF_HOME"]/hub`,
  then `home/.cache/huggingface/hub`. Tamra lists `models--*/snapshots/*/**/*.gguf`.
  - A snapshot file that is a symlink to `blobs/<64 hex>` gets that hex as its `sha256`.
  - Windows without symlink support stores the real file in the snapshot, so `sha256` is
    `None`.
  - The name is the file stem.
- **Rules for every source:**
  - Files whose name starts with `mmproj` are skipped.
  - Of a split GGUF (`-0000N-of-0000M.gguf`), only the first part is listed, because
    llama-server loads the rest itself.
  - Unreadable folders, bad JSON, missing blobs and files that do not start with `GGUF`
    are skipped and logged at debug level. A broken source never stops the other
    sources from being scanned.

### Linked imports: setting `linked_models`

`Settings.linked_models` is a tuple of `{"path": str, "size": int, "sha256": str}` entries.

- `path` is absolute and ends in `.gguf`.
- `size` is a non-negative int.
- `sha256` is a 64-character lowercase hex string.

Tamra hashes the file once, at import, the way it already does to match the catalog. When
listing, Tamra only compares the stored size with the file's current size. A file whose
size changed is treated as a different file and is not listed.

The server owns this setting:

- `PUT /api/settings` answers 400 when the request contains `linked_models`.
- `_settings_payload` leaves the field out.

Only the import and unlink endpoints change it, through `Core`.

### `local_model_id` forms

| id | Meaning |
|---|---|
| catalog id (`qwen3-8b`) | That catalog entry, from any source whose sha256 matches the pinned one |
| `import:<file name>` | An uncatalogued GGUF in Tamra's `models\` (unchanged) |
| `file:<absolute path>` (new) | An uncatalogued GGUF that is linked or was found by the scan. Any other path is never used. |

### `Core.resolve_local_model`

1. **The wanted id is a catalog id.** Tamra uses the file in `models\` (size check, as
   now). If there is none, it uses a linked file whose stored sha256 equals the pinned
   one. If there is none, it uses a scanned file whose sha256 equals the pinned one.
2. **The wanted id is `import:` or `file:`.** Tamra uses that file while it is still
   listed.
3. **Fallback.** Tamra uses the first catalog LLM that is available from any source. If
   none is, it uses the first uncatalogued file, in this order: Tamra's `models\`, then
   linked files, then scanned files.

So a machine with only Ollama models works in local mode without any download.

The labels follow the same rules:

- A catalog match shows the catalog name.
- An Ollama model shows its `<model>:<tag>` name.
- Any other file shows its file stem.

`Core` caches the scan result for 10 seconds, because `/api/models` is polled every second
while the model menu is open. The code marks this with a `shortcut:` comment. A successful
import or unlink clears the cache.

## 2. Import, API and UI

### `Core.import_model(path)`

`Core` decides between linking and copying, because it owns the settings. `ModelManager`
stays file-only. Its copy path is unchanged, and it gains a way to hash and validate a
file without copying it.

1. Tamra hashes the file and matches it against the catalog, as now.
2. A file that matches the embedding or OCR entry is copied, as now.
3. An LLM file is either a catalog match or an uncatalogued GGUF:
   - **On a removable or CD-ROM drive.** It is copied into `models\` as now. Its id is the
     catalog id or `import:<name>`.
   - **On any other drive, network drives included.** It is not copied. Tamra adds it to
     `linked_models` and selects it. Its id is the catalog id when the hash matches, else
     `file:<path>`.
   - **Importing a path that is already linked.** Tamra refreshes its size and sha256 and
     selects it, without adding a second entry.
   - **Name clash.** A linked file is not refused for having a catalog file's name with
     other content. That check exists to keep `models\` from holding a fake pinned file,
     and a linked file never enters `models\`. Without this exception, LM Studio's
     `Qwen3-8B-Q4_K_M.gguf` from lmstudio-community would be refused.
   - **GGUF check.** The `GGUF` magic-bytes check still applies.
4. **Drive type.** A small helper calls `GetDriveTypeW` on the path's drive root through
   `ctypes`, from the standard library. `DRIVE_REMOVABLE` (2) and `DRIVE_CDROM` (5) mean
   copy. If the call fails or the platform is not Windows, the drive counts as fixed and
   the file is linked. Tests monkeypatch the helper.
   - Known limit: some external USB disks report as fixed drives, so they are linked. When
     such a disk is unplugged, the model is not listed and resolution falls back.

### API

| Endpoint | Change |
|---|---|
| `POST /api/models/import` | The response adds `"linked": bool` |
| `POST /api/models/unlink` `{path}` (new) | Removes the entry from `linked_models` and never deletes the file. Answers 204, or 404 when the path is not linked. |
| `GET /api/models` → `local[]` | Adds `source`: where the installed copy of a catalog entry is (`"tamra"`, `"linked"`, `"ollama"`, `"lmstudio"`, `"huggingface"`), or `null` when the entry is not installed |
| `GET /api/models` → `uncatalogued[]` | Each item adds `source` and the full `path`. The list holds `models\` files, linked files and scanned files. |
| `PUT /api/settings` | Answers 400 when the request contains `linked_models` |

### UI

- **Settings → Local model list:**
  - The detail line names the source, for example "From Ollama · 5.0 GB", "Linked file ·
    5.0 GB" or "Imported".
  - The full path is the model name's `title`.
  - A catalog entry found in another tool shows "Installed · From Ollama" instead of a
    download button.
- **Remove from list.** A linked row has a "Remove from list" button. Its `aria-label`
  names the model, and its hint says that the file is not deleted. It calls `unlink` and
  then refreshes. Scanned rows have no such button; the user removes those in their own
  tool.
- **Import notes:**
  - When the file was linked: "Using {file} where it is; it was not copied."
  - When it was copied from a removable drive: "Copied {file} from the removable drive."
  - `settings.importHint` says that files are normally used in place.
- **Model menu.** The note under an uncatalogued entry names its source instead of
  "Imported".
- **Types and strings.** `ui/src/types.ts` gets the new fields and `ui/src/test-models.ts`
  the new fixtures. Every new string is added to both `en.ts` and `th.ts`.

**Known limit.** While llama-server runs a model from Ollama's store, the file is open, so
Windows refuses `ollama rm` of that model. The user must switch models or close Tamra first.

## 3. Errors and testing

### Error handling

| Case | Behavior |
|---|---|
| Bad manifest JSON, missing blob, file without `GGUF` magic, or an unreadable folder | Skipped and logged at debug level. `/api/models` never fails because of a source. |
| Linked file missing or its size changed | Not listed, and the entry is kept because the drive may come back. If it was selected, resolution falls back (section 1). |
| File removed while selected | The existing `model_missing` error in `LocalLLM.client()` |
| llama.cpp does not know the model architecture | New `ErrorReason` `model_unsupported`, added in both `tamra.llm.base` and `ui/src/types.ts`. When llama-server fails to start and its log contains `unknown model architecture`, the error says that Tamra cannot run this model type yet, names the architecture, and asks the user to choose another model. It replaces the bare "failed to start; see log". Strings are added in en and th through `errorText`. |
| `GetDriveTypeW` unavailable | The file is linked, and the import does not fail |
| `unlink` with a path that is not linked | 404 |

### Tests (written first, TDD)

- **`tests/test_external.py`** uses fake stores under `tmp_path`.
  - Ollama:
    - A valid manifest gives `qwen3:8b`, the blob path and the sha256.
    - A manifest with a projector layer is skipped.
    - Bad JSON is skipped, and so is a blob without the `GGUF` magic.
    - Two tags that share one blob give one entry, named by the tag that is not `latest`.
    - `OLLAMA_MODELS` is honored.
  - LM Studio:
    - Files are found in all three locations, and through the home pointer.
    - `mmproj*` files are skipped, and so are split parts after the first.
  - Hugging Face:
    - A plain snapshot file has `sha256=None`.
    - A symlink to `blobs/<hex>` takes its sha256 from the blob name. The test is skipped
      when the OS refuses to create symlinks.
    - `HF_HUB_CACHE` is honored.
  - No store at all gives `[]`.
- **`tests/test_settings.py`** checks valid and invalid `linked_models` values: a value
  that is not a list, a relative path, a path that does not end in `.gguf`, a negative
  size, and a bad hash.
- **Core tests:**
  - `resolve_local_model` in every branch:
    - A catalog id resolves through a linked file and through a scanned file.
    - A `file:` id whose path is not listed is never used.
    - A linked file whose size changed is not used.
    - A machine with only an Ollama model resolves to that model.
  - `import_model`:
    - A file on a fixed drive is linked, and a file on a removable drive is copied (the
      drive helper is monkeypatched).
    - An embedding file is still copied.
    - Importing a path that is already linked refreshes it.
    - A file with a catalog file's name but other content is linked.
  - The 10-second scan cache, and its clearing after an import.
- **Server tests:**
  - `/api/models` returns `source` and `path`.
  - `import` returns `linked`.
  - `unlink` answers 204, and 404 for a path that is not linked.
  - `PUT /api/settings` with `linked_models` answers 400.
  - The settings payload leaves out `linked_models`.
- **LLM tests:** a llama-server log containing `unknown model architecture` gives
  `model_unsupported`.
- **UI (Vitest):**
  - `SettingsLocal`:
    - Rows show their source.
    - "Remove from list" calls `unlink` and then refreshes.
    - Both import notes appear.
  - `ModelMenu` shows the source note.
  - `test-models.ts` gets the new fixtures.
- **Manual check.** On the dev machine, Tamra imports by link from a folder outside
  `models\`, and `scripts/exe_smoke.py` still passes. This machine has neither Ollama nor
  LM Studio, so the user checks real Ollama and LM Studio installs.

### Docs

When this is implemented, rewrite the Import bullet in
`docs/superpowers/specs/2026-10-02-tamra-design.md` (§ models, "Import: for offline
machines …"). It should say:

- LLM files on fixed drives are linked.
- Files on removable drives, and every embedding and OCR file, are copied.
- Models from Ollama, LM Studio and the Hugging Face cache are found automatically.
