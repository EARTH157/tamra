# Project notes: tell the model about your situation once per project

Status: implemented (plan docs/superpowers/plans/2026-10-24-project-notes.md), revised after the
real-model check (see "Why the notes are a source").
Date: 2026-10-24.

## Goal

Each chat starts from nothing today. A user who is "the tenant of unit 1204" or who wants
"answers as a numbered list" has to say so again in every chat. A project gets **notes**: a
short text the model reads in every chat of that project. The notes help the model
understand the question and shape the answer. In Answer mode they are also **a numbered
source, "Your project notes"**. A fact the model takes from them cites the notes, never a
document. So every fact in an answer still carries an `[n]` that shows where it came from,
and answers stay checkable (spec §1).

The notes cannot change the answer's language: the rule "answer in the language of the
question" (spec §6) comes first.

### Why the notes are a source

The first design gave the notes to the model as guidance that is "not a source: never state a
fact from them". On qwen3.5:4b, with the notes "I moved in on 3 March", the question "When did
I move in?" was answered "3 March" in 8 of 8 runs. In 4 of them the model cited the lease, `[1]`,
which says 1 March. That false citation breaks the promise that answers are checkable. Rewording
did not help: a stronger rule still gave the notes' date in 7 of 8 runs, and putting the notes
before the rules gave it in 5 of 8, citing the lease. A small model cannot know a fact and leave
it out. With the notes as source `[3]` the model cited `[3]` in 8 of 8 runs and the lease in
none, and a question about the lease still cited the lease (3 of 3).

Out of scope (YAGNI): notes that apply to every chat (global notes), notes for general chats,
notes that change retrieval, per-chat notes, and a history of note edits.

## 1. Data and API

### Storage

- Schema v6 adds `collections.notes TEXT NOT NULL DEFAULT ''`. A database at v5 is upgraded in
  place, and every existing project gets empty notes. Deleting a project deletes its notes.
- `Collection` gains `notes: str = ""`. The store gains `set_collection_notes(id, notes) -> bool`
  (False if the project is unknown).

### What notes may hold

`Core` cleans and checks the text before it is saved:

- `\r\n` and `\r` become `\n`. Leading and trailing whitespace is removed. An empty result
  means the project has no notes.
- At most **1,000 characters** (code points) after cleaning. That is at most about 1,000
  tokens even in Thai (spec §6 counts a Thai character as one token), so the notes always
  fit beside the sources in an 8k window and are never cut.
- New lines and tabs are kept. Other control characters (Unicode Cc) are refused, and so are
  the bidirectional overrides and isolates (U+202A–U+202E, U+2066–U+2069). Other format
  characters are allowed, so a zero-width space pasted with Thai text does not fail.
- A refused text raises `BadNotes` (a `ValueError`), and nothing is saved.

### API

- `PATCH /api/projects/{id}` takes `{"name"?: str, "notes"?: str}`. Both fields are checked
  before either is saved, so a refused request changes nothing. A bad `notes` is a 400 with
  the fixed detail "Project notes must be at most 1,000 characters, without control
  characters." An unknown project is a 404, as today.
- Every project payload (`GET /api/projects`, the replies of the project routes) gains
  `"notes": str`.

## 2. Prompt

Notes are read from the chat's **home project** when the question is asked, in Answer and
Chat mode. A chat that searches several projects uses only its home project's notes. A
general chat (no home) has none. An edit applies from the next question on. The answer being
written keeps the notes it started with.

When the notes are empty, the prompts are byte for byte the ones used today.

### Answer mode: the notes are the last source

- Retrieval does not change. The documents' passages are found and numbered `[1]..[k]` as
  today, and the not-found gate still looks only at them. When no passage passes the
  threshold, the reply is "not found" without a model call, whatever the notes say. Answer
  mode answers from the documents, and the notes only add to them.
- When the chat's home project has notes, they become source `[k+1]`: a `SourceRecord` with
  `rel_path` "Your project notes", `text` the notes, `location` `{"kind": "notes"}`, and no
  chunk, file or file hash. In the prompt it is listed after the documents as
  `[k+1] Your project notes` followed by the notes, with no location.
- `SYSTEM_PROMPT` gets one more rule after "Be concise.":

```
- Source [k+1] is the user's own notes for this project: their situation and how they want
  answers. Follow how they want answers. A fact taken from the notes must cite [k+1], never a
  document's number.
```

- The notes source is sent in the `sources` event and saved with the answer like any source
  (`message_sources`: `chunk_id` and `file_id` null, `rel_path` "Your project notes",
  `location_json` `{"kind": "notes"}`). An old chat keeps showing the notes the model saw, even
  after the notes change.
- Search only mode shows the documents' passages and never the notes.
- Auto-citation (spec §6) may cite the notes, when an uncited answer was clearly taken from
  them.

### Chat mode: the notes guide the reply

Chat mode has no sources and no `[n]` markers. `CHAT_PROMPT` gets the notes after its rules:

```
- The user's notes below describe their situation and how they want answers. Use them to
  understand the question and to shape the answer. They are not a source: never state a
  fact from them as the answer, and never cite them.

The user's notes for this project:
<notes>
{notes}
</notes>
```

The UI already labels a Chat reply as a general answer that is not from the documents.

The query text is still the question plus the previous question (spec §6), and notes never
enter it.

## 3. Token budget and the context meter

- The notes are part of the system prompt, so the budget already counts them. History gets
  what the system prompt, with its notes and sources, and the question leave, and in Answer
  mode at most a quarter of the room, as today. Documents' passages are not reduced for notes.
- The `context` event's `parts` gains `notes`. In Answer mode it is the notes source's block
  plus its rule; in Chat mode it is the notes text (rule and block). `instructions` and
  `sources` no longer include them, and `source_count` counts the documents' passages only.
  The parts still add up to `prompt_tokens`.
- The UI's Details list gets a row "Project notes", shown only when the part is not 0. A
  context saved with a chat before this change has no `notes` part, and the UI reads a
  missing part as 0.

## 4. UI

- **Project settings dialog:** a "Notes" field (a textarea, about six lines) under the name.
  - The help text says: "Tamra gives these notes to the model in every chat of this project,
    to understand your questions and shape its answers. An answer that uses them cites them
    as "Your project notes", apart from your documents."
  - A counter "123 / 1,000" counts code points, as the core does, so it agrees with the
    core's limit. It turns to the error colour above the limit, and Save is disabled then.
  - Save sends one `PATCH` with the fields that changed. A refusal shows in the dialog, which
    stays open.
- **Sidebar project card:** a link "Add notes" (no notes) or "Notes" (some notes) beside
  "Change folder". It opens the project settings dialog with the Notes field focused.
- **The notes as a source:** the UI tells a notes source by `location.kind === "notes"`.
  - Its card, the source panel's title and a copied answer name it "Your project notes"
    (translated), with no location line.
  - It has no file, so the panel shows its saved text and no "Open file" button. Locating it
    is not asked for.
  - "Check source" works on it like on any source: it compares the selection with the saved
    notes. The core reports a notes source as unchanged, so the panel never warns that
    "the document changed".
- All new text is in English and Thai (`ui/src/strings`).

## 5. Errors and edge cases

| Situation | Behavior |
|---|---|
| Notes over 1,000 characters or with a refused character | 400 with the fixed detail; the dialog shows it in the user's language; nothing saved |
| Notes edited while an answer is written | The answer keeps the notes it started with; the next question uses the new ones |
| Project deleted | Its notes go with it |
| Chat moved to another project | It uses the new home project's notes from its next question |
| Notes contain text that looks like instructions or a fake source | The user wrote them for their own chats, so they are trusted. They are their own numbered source, so whatever an answer takes from them cites the notes |
| Notes ask for another answer language | The question's language still wins (spec §6) |
| A question only the notes can answer | Answer mode: "not found" when no document passage passes the threshold; when one does, the model may answer from the notes and cite them. Chat mode: the reply may use them |
| MCP (`Tamra.exe mcp`) | Unchanged: it searches and returns passages, and no model reads the notes |

## 6. Tests (written first, TDD)

- **Store:** migrating v5 to v6 gives every project empty notes, and `set_collection_notes`
  saves notes and returns False for an unknown id.
- **Core and API:**
  - The cleaning rules: `\r\n` becomes `\n`, the text is stripped, and an empty result is
    empty notes.
  - Exactly 1,000 code points are accepted and 1,001 are refused. A Thai text and a zero-width
    space are accepted. A bell, U+202E and U+2066 are refused.
  - A PATCH with a good name and bad notes changes neither.
  - The payloads carry `notes`, and the token is still required.
- **Prompt and sources:**
  - Empty notes give today's prompts exactly.
  - With notes, Answer mode lists them as source `[k+1]` "Your project notes" after the
    documents. The rule names that number, and there is no `<notes>` block.
  - The `sources` event and the saved answer both include the notes source.
  - Search only mode and the not-found gate never include the notes.
  - Chat mode has the rule and the block after its rules.
  - The chat's home project's notes are used, not those of another project in its scope,
    and a general chat has none.
- **Context event:** `parts.notes` counts the notes (source block and rule in Answer mode,
  rule and block in Chat mode). `sources` and `source_count` cover the documents only, and the
  parts still add up to `prompt_tokens`.
- **Core:** `source_changed` is False for a notes source.
- **UI:**
  - The dialog edits and saves notes, and the counter counts code points.
  - Save is disabled over the limit, and a refusal is shown.
  - The card link says "Add notes" or "Notes" and opens the dialog on the field.
  - The Details row shows only when there are notes, and an old saved context without the
    part still renders.
  - A notes source shows as "Your project notes", with no "Open file" and no location line,
    and a copied answer names it so.
- **With a real model, before the PR:** with the notes "Answer as a numbered list." and "I am
  the tenant of unit 1204. I moved in on 3 March.", check that:
  - "When did I move in?" never cites a document for 3 March. When it gives that date, it
    cites the notes source.
  - "How long is the lease term?" cites the lease.
  - Record how often the style is followed and which model was used. Small models follow the
    style less often when the notes are a source (0 of 3 numbered lists in the experiment);
    that is accepted, the checkable citations come first.

## 7. Docs

The behavior spec (`2026-10-02-tamra-design.md`) is updated with this design:

- §4 lists `collections.notes`.
- §6 gets a "Project notes" bullet, and its "Prompt" and "Context and token counts" bullets
  name the notes source, the Chat-mode notes block and the `notes` part.
