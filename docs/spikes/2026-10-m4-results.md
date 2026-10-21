# M4 results

## Exit run

M4 adds projects: several folders of documents, each with its own search index and chats, and a
search scope per chat ("Search in"). `dist/Tamra/Tamra.exe` was built on the dev machine with
`./scripts/build.ps1` as is (including `npm ci`) at commit `90c1ffa`, and checked with
`scripts/exe_smoke.py` and the packaged selfcheck. Build output: `Tamra.exe` is 14.4 MB and the
`dist/Tamra` folder is 248 MB (no new dependency). Models: bge-m3 int8 and the
Qwen2.5-0.5B-instruct Q4_K_M dev model, nothing downloaded. The test suite had 825 passing tests
(12 `assets` tests deselected), the UI suite 450 tests, and the lint and format checks were clean.

**What the exit criterion covers**

- The packaged smoke passes with the new project checks (below).
- The controller checked the UI in the browser against the dev core: see "Browser check".
- The real-window check (pywebview) is the owner's.

## Packaged selfcheck

Exit code 0; all five checks `ok` (`sqlite`, `documents`, `providers`, `embedding`, `llm`), run as
`Tamra.exe selfcheck --embed-model-dir .models\bge-m3 --llm-model
.models\qwen2.5-0.5b-instruct-q4_k_m.gguf --report r.json` through `Start-Process -Wait`.

| Measure | Value |
|---|---|
| Embedding (packaged) | 3.6 passages/s, model load 3.3 s |
| Local LLM start | 2.2 s, first token 0.04 s |
| Local LLM speed | 224 tokens/s (56 tokens) |
| GPU used (Vulkan) | yes |

The embedding rate is lower than M3's 7.0 passages/s (a first run at the previous commit gave
4.7 passages/s). The selfcheck embeds a handful of passages once, so treat the number as noise;
M4 changed nothing in the embedder.

## Smoke test

The exe in `--dev` mode with a scratch data folder and a scratch models folder holding only
bge-m3. Exit code 0, no problems, 3 of 3 answerable questions cited, and no `llama-server.exe`
left over. Since the audit fixes the packaged exe refuses `--dev` unless `TAMRA_DEV_TOKEN` holds a
token of at least 24 characters; the smoke makes a fresh one per run, uses it as the API token
and never prints it. The M1 to M3 steps are unchanged and still pass: startup 4.7 s, import of the
dev GGUF (uncatalogued), indexing the two projects (6 files) in 1.5 s, Thai, English and Chinese
answers cited with provider `local`, the not-found reply for the off-topic question, a
`think: true` request accepted, the folder watcher in 2.5 s, attribution, locate (4 highlight
boxes), the PDF page PNG and the text route, and API mode with the throwaway key (provider
`openai`, then back to `local`, key deleted and checked).

**What changed in the smoke.** It builds the eval corpus into one scratch folder and copies six
documents into two sibling folders inside the scratch directory (not under the data directory:
folders that overlap Tamra's data folder are refused):

- **Company:** `company-history.txt`, `hr-leave-policy.docx`, `it-security-policy.md`.
- **Contracts:** `apartment-lease.pdf`, `shipping-policy.pdf`, `furniture-warranty.txt`.

It creates the two projects through `POST /api/projects`. The M1 to M3 questions run in chats
whose scope is both projects, and the watcher file goes into the Company folder. The other four
corpus documents (canteen rules, air purifier manual, meeting notes, travel expenses) are not used
by the smoke.

**New in M4** (the lease question, `en-lease-rent`, asked in a chat made in Company):

| Step | Result |
|---|---|
| Company-only chat asks the lease question | not a not-found reply, but every source is from Company (`it-security-policy.md`, `watcher-check.md`, `hr-leave-policy.docx`, `company-history.txt`); none from Contracts, and no `apartment-lease.pdf` |
| `PUT /api/chats/{id}/scope` to Company + Contracts | 200, scope `[1, 2]` |
| The same question again | `Contracts: apartment-lease.pdf` is source 1 (also `Contracts: shipping-policy.pdf` and two Company files) |
| `PATCH /api/chats/{id}` to Contracts, then `GET /api/chats` | 200; the chat is listed with `project_id` 2 (Contracts); its scope is unchanged, `[1, 2]` |
| Rename Company to "Company records" | 200; the list shows the new name. Renaming Contracts to "company RECORDS" gives 409 |
| `POST /api/projects/{contracts}/rebuild` | 202; its files went back to pending and were indexed again (3 indexed); Company's counts were unchanged; the lease is cited again, and sources now say "Company records" |
| `DELETE /api/projects/{contracts}` | 200, `chats_deleted` 1 (the moved chat); the project list holds only "Company records"; every remaining chat searches `[1]` only; the 3 files in the Contracts folder are still there |

The smoke asserts on the sources, not on the answer text: the 0.5B model still writes a made-up
sentence for the Company-only chat ("The monthly rent is 10,000 baht"), which is a model quality
issue. No source in that chat came from the lease, which is the point of the check.

**Timings** (packaged exe, 0.5B model; two runs, before and after the last two fixes): startup 5.2
and 4.7 s, indexing 1.7 and 1.5 s, attribution first call 0.61 and 1.30 s (repeat 0.70 and
0.86 s), page render at scale 1.5 0.046 and 0.079 s, watcher 2.5 s in both. The attribution
sentence differs between runs because the 0.5B model words the answer differently; in the second
run it wrote a rent of 10,000 baht and the matcher labelled it "partial" (the number is not in the
lease), in the first run it wrote 18,500 baht and the label was "strong".

## Migration check

An M3 database (schema version 2) upgrades in place and keeps its data. Checked on a database
built from the v1 and v2 schema (one collection "Eval corpus" with a file, two chats with a
question and an answer each, and a stored source), then opened with the current `Store`:

- `PRAGMA user_version` went from 2 to 3.
- The one collection became the first project ("Eval corpus", id 1) and its file kept its status.
- Both chats kept their messages (`q1`, `a1 [1]`, `q2`, `a2 [1]`), and each got project 1 as its home
  and `[1]` as its scope.

The same path is covered by `tests/test_store_projects.py` (a database with no project gets
homeless chats that the first project adopts; duplicate names are renamed before the unique
index; a failed migration rolls back). The controller also checked it on a real scratch database
in the browser (below).

## Browser check

The controller ran the dev core and the UI dev server and drove the page (accessibility tree and
scripts; screenshots timed out):

- **Task 5 (project tree).** A real v2 scratch database migrated to one project "Eval corpus" that
  held both existing chats. The **New project** dialog created "Contracts" and indexed its 2 files.
  **Move to project** moved the rent chat under Contracts. The menu item had no accessible name; this
  was fixed.
- **Task 6 (Search in).** A new chat in Contracts showed the chip "Search in: Contracts" and its
  answer cited only Contracts files. Ticking "Eval corpus" changed the chip to "Contracts + 1", and
  the sources were labelled "Eval corpus · ..." and "Contracts · ...". The first run showed an old
  `Composer.tsx` until the UI dev server was restarted (see "Not done in M4").
- Still to do for the owner: the real pywebview window.

## Audit fixes

After Task 6 the controller audited the whole repository (`ponytail-audit`) and fixed the findings
in two rounds, plus a small follow-up.

**Round 1** (`ca2190b`)

1. **Single instance.** A second Tamra on the same data folder is refused: a named mutex
   (`Local\Tamra-<hash of the folder>`) is taken first. The window build shows "Tamra is already
   running."; dev mode raises. A run with its own `TAMRA_DATA_DIR` never collides.
2. **Safe reconcile.** An unlistable folder aborts the scan instead of looking empty, so reconcile
   cannot delete the files of a folder that is only temporarily unreadable.
3. **Room to think with a local model.** The thinking token headroom (2048) now applies to local
   models too, so "think longer" no longer cuts the answer off.
4. **File size cap.** Files above 200 MB are skipped with "file too large (over 200 MB)", before they
   are hashed or parsed.
5. **DOCX tables.** The parser reads body paragraphs and tables in order; each table row is one
   unit with its cells joined by " | ". Paragraph indices for documents without tables are
   unchanged.
6. **No dev mode in the exe.** The packaged exe refuses `--dev` (a fixed port and token would be a
   back door).

**Round 2** (`cccae09`)

1. **Dev mode of the exe, with a token.** `--dev` in the exe works only with `TAMRA_DEV_TOKEN` of at
   least 24 characters; that value is the API token. The port stays 8765. Without it the exe exits
   with 2.
2. **Precise reconcile.** Folders and files that could not be read are recorded ("Could not read:
   ...") and only the files under them are kept; the rest of the project is reconciled normally and
   the project is no longer blocked.
3. **Merged cells.** A vertically merged DOCX cell is read once, in its first row.
4. **Nested tables.** Rows of a table inside a cell follow their parent row.
5. **Mutex calls.** Proper `argtypes` and `restype`, and a failed `CreateMutexW` raises instead of
   passing for "already running".
6. **Per-file stat errors** are recorded as unread and never delete the file's index.
7. **Size check first.** It runs before the embedder is loaded.

**Controller follow-up** (`90c1ffa`): a horizontally merged DOCX cell is read once, and unread
paths are compared without case.

A DOCX file indexed before these fixes keeps its old table text and paragraph indices until its
project's index is rebuilt (**Project settings > Rebuild index**), because its content hash did not
change.

## Owner requests during M4

These came from the owner's own use of the M4 exe and are on the same branch.

1. **Search model in Settings** (`47eb32d`, `279d911`). With a chat model downloaded but no bge-m3,
   every question failed. Settings > AI model now has a "Search model" row that downloads it. A
   question that cannot be embedded is not saved to the chat; when the cause is the missing model
   files (`EmbedderUnavailable`) the error carries `reason: "embedding_missing"` and the UI links to
   Settings. After the download, every project is reconciled, so files skipped earlier get indexed.
2. **Real PDF page in the source panel** (`f4a43f4`, `93e31d1`). For a PDF source the panel shows the
   rendered page with the passage marked, instead of the text snapshot. Prev/next keeps the page
   mounted and moves only the marks. If the file cannot be located, the panel falls back to the
   snapshot. The page zooms in steps from 100% to 300% of the panel width; another file starts at
   100%.
3. **LaTeX in answers** (`6c1e5fc`, `93e31d1`). `$...$`, `$$...$$`, `\(...\)` and `\[...\]` render
   with KaTeX 0.19.0 (MIT) as MathML: no fonts or CSS, `trust: false`, and errors shown as text. A
   `$` follows pandoc's rule, so prices such as "$5 and $10" stay text. A selection that starts or
   ends inside a formula takes the whole formula.
4. **Conversation memory** (`6c53c0f`). The owner found that follow-ups lost the thread. Chats
   were saved, but the model saw only the previous exchange, its answer cut to 600 characters. It
   now sees the last 4 exchanges of the chat (`Store.recent_exchanges`), oldest first. Earlier
   answers lose their `[n]` markers, since their sources are no longer in the prompt. Each earlier
   question and answer is cut to 600 characters. A question without an answer (cancelled, failed,
   or search mode) or with a not-found reply is left out. Retrieval still adds only the previous question to the query.

The PDF page and the formulas are checked by unit tests only; their look in the WebView2 window is
the owner's check.

## Deviations from the spec

The spec (`docs/superpowers/specs/2026-10-02-tamra-design.md`) §4 to §6 now describe what was
built. The places where the first version differed:

- **Home project and search scope.** The spec said a chat selects one or more collections. A chat
  now has a home project (`chats.collection_id`, which sets where it is listed) and a search scope
  (`chat_collections`, defaulting to the home project). "Move to project" changes the home; the
  scope follows when the old home was the only project in it.
- **Deleting a project** deletes its index and its chats, after a confirmation that names the chat
  count. A chat that also searched it only loses it from its scope, and a scope left empty falls
  back to the home project.
- **One worker for all projects.** Reconcile runs project by project, then pending files are
  indexed oldest first across projects; status is per project.
- **Refused folders.** Tamra refuses a drive or share root, its own data or models folder (or a
  parent of them), and a folder that is the same as, inside, or around another project's folder.
  Symlinks and junctions inside a folder are skipped.
- **Stale projects.** A project built with another embedding model is left out of a search with a
  notice. When every project in scope is stale the question gets an error asking for a rebuild.
- **Project settings.** The General tab's "Documents folder" and "Search index" rows moved to a
  per-project "Project settings" dialog (name, folder, index status, rebuild).
- **"Search in" chip.** The spec required a way to choose the projects; the frames omit it, so it
  is a chip in the composer.

## Not done in M4

- **Chat export ("Export with proof") and the suggestion cards** are later ideas (Idea 5 and the
  empty-state suggestions). They are not shown in M4.
- **Quality of the 0.5B dev model.** It often writes a plausible sentence that the documents do
  not support (the "10,000 baht" rent above, a "60 days" in the browser check). The retrieval and
  the source checks are what M4 tests; answer quality needs the real catalog models.
- **Scope change saved while switching chats** (Task 6, nit 1). If the user opens another chat
  while a scope change is still being saved, the core applies it, but the chat list (and so the
  chip of the chat that was left) keeps the old scope until the list is next reloaded.
- **"Could not read" can vanish for a while.** The error is cleared at the start of every indexer
  pass, so a pass that does not reconcile that project hides it until the next reconcile.
- **A failure of the single-instance check exits silently.** If Windows refuses to create the
  mutex (not "already running"), the windowed app exits before logging is set up.
- **A chat created while the first project is being created** can stay without a project; moving
  it to a project repairs it.
- **Batching in `source_payloads`** (Task 3, minor 3): one store call per source file for the
  project label; fine for a handful of sources, to batch if the number grows. A second, minor
  nit from the Task 6 re-review is also deferred.
- **Admin-share aliases** such as `\\localhost\C$\...` are not caught by the folder checks (they
  can name a folder that overlaps Tamra's data folder). Drive roots and the `\\?\` and `\\.\` forms
  are refused.
- **Paths in index errors.** `index.error` and `problems[].error` can contain a path of the user's
  own folder; the other API errors are fixed strings.
- **The tree polls every 10 s when idle** and reloads on a project action. A change made by another
  window shows after at most 10 s.
- **`RowMenu` is separate from `PopMenu`.** Two menu implementations to keep in step.
- **Stale projects are not marked in the "Search in" menu,** only in the notice shown when a
  question is asked.
- **Vite dev server and the file watcher.** The dev server served an old `Composer.tsx` after the
  file changed, until it was restarted. If a dev UI change does not show up, restart
  `npm --prefix ui run dev`.
- **The smoke opens no window.** It uses the exe's dev mode, so pywebview, the folder picker and the
  WebView2 paths are the owner's real-window check, as in M3.
- **The M3 list** (viewer pane scrolling, no `aria-live` on Check source, WebView2 selection edge
  cases, and the rest) is unchanged, except that `Core.file_path` now resolves each file from its
  own project's folder.
- **Anthropic and the real window** are still the owner's checks, as in M2 and M3.
- **Zoom buttons and keyboard focus.** In the panel and in the document viewer, the zoom button
  that reaches the end of the range is disabled and drops focus, and the percent is not announced.
- **Citations in follow-ups are not measured.** Earlier answers reach the model without `[n]`
  markers; whether a small local model then cites less often in the 2nd and later answers is
  unchecked. The scroll to the first mark after a zoom has no unit test (jsdom loads no image).
