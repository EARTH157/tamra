# Project notes: tell the model about your situation once per project

Status: approved design, not implemented. Date: 2026-10-24.

## Goal

Each chat starts from nothing today. A user who is "the tenant of unit 1204" or who wants
"answers in Thai, as a list" has to say so again in every chat. A project gets **notes**: a
short text the model reads in every chat of that project. The notes help the model
understand the question and shape the answer. They are **not a source**. Every fact in an
answer still comes from the documents and carries an `[n]` citation, so answers stay
checkable (spec §1).

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

When there are notes, the **Answer** prompt (`SYSTEM_PROMPT`) gets one more rule, and a notes
block between the rules and the sources:

```
- The user's notes below describe their situation and how they want answers. Use them to
  understand the question and to shape the answer. They are not a source: never state a
  fact from them as the answer, and never cite them.

The user's notes for this project:
<notes>
{notes}
</notes>
```

The **Chat** prompt (`CHAT_PROMPT`) gets the same rule and block after its rules. In Chat
mode there are no sources, so "not a source" keeps the model from presenting a note as
something it read in the documents.

Why the system prompt, before the sources:

- The rules, the notes and the sources stay apart, and the notes cannot pass for a source.
- Within one project the start of the prompt (rules, then notes) is the same for every
  question, so llama-server can reuse its cache for that part.
- The other options were worse. As a first exchange of the history, the notes would be cut
  by the history budget, and a small model reads them as conversation. Added to every
  question, they would mix into the question and the history.

Retrieval does not change. The query text is still the question plus the previous question
(spec §6), and notes never enter it.

## 3. Token budget and the context meter

- The notes are part of the system prompt, so the budget already counts them. History gets
  what the system prompt, with its notes and sources, and the question leave, and in Answer
  mode at most a quarter of the room, as today. Sources are not reduced for notes.
- The `context` event's `parts` gains `notes`: the estimated tokens of the notes block (rule
  and block). `instructions` no longer includes them. The parts still add up to
  `prompt_tokens`.
- The UI's Details list gets a row "Project notes", shown only when the part is not 0. A
  context saved with a chat before this change has no `notes` part, and the UI reads a
  missing part as 0.

## 4. UI

- **Project settings dialog:** a "Notes" field (a textarea, about six lines) under the name.
  - The help text says: "Tamra gives these notes to the model in every chat of this project,
    to understand your questions and shape its answers. They are not a source: answers still
    cite your documents."
  - A counter "123 / 1,000" counts code points, as the core does, so it agrees with the
    core's limit. It turns to the error colour above the limit, and Save is disabled then.
  - Save sends one `PATCH` with the fields that changed. A refusal shows in the dialog, which
    stays open.
- **Sidebar project card:** a link "Add notes" (no notes) or "Notes" (some notes) beside
  "Change folder". It opens the project settings dialog with the Notes field focused.
- All new text is in English and Thai (`ui/src/strings`).

## 5. Errors and edge cases

| Situation | Behavior |
|---|---|
| Notes over 1,000 characters or with a refused character | 400 with the fixed detail; the dialog shows it in the user's language; nothing saved |
| Notes edited while an answer is written | The answer keeps the notes it started with; the next question uses the new ones |
| Project deleted | Its notes go with it |
| Chat moved to another project | It uses the new home project's notes from its next question |
| Notes contain text that looks like instructions or a fake source | The user wrote them for their own chats, so they are trusted. The notes block and its rule keep them apart from the sources, and they never become a citation |
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
- **Prompt:**
  - Empty notes give today's prompts exactly.
  - With notes, both modes have the rule and the block, and in Answer mode the block comes
    before `Sources:`.
  - The chat's home project's notes are used, not those of another project in its scope,
    and a general chat has none.
- **Context event:** `parts.notes` is the notes block's estimate, `instructions` drops by the
  same amount, and the parts still add up to `prompt_tokens`.
- **UI:**
  - The dialog edits and saves notes, and the counter counts code points.
  - Save is disabled over the limit, and a refusal is shown.
  - The card link says "Add notes" or "Notes" and opens the dialog on the field.
  - The Details row shows only when there are notes, and an old saved context without the
    part still renders.
- **With a real model, once, before the PR:** with notes "Answer in Thai, as a numbered
  list" and "I am the tenant of unit 1204. I moved in on 3 March", check that:
  - an Answer-mode reply follows the style and still cites the documents;
  - a question whose answer is only in the notes (for example "When did I move in?", with
    the date only in the notes) is not answered from them. The reply is "not found" or
    says the documents do not tell, and no `[n]` points at the notes.

## 7. Docs

The behavior spec (`2026-10-02-tamra-design.md`) is updated with this design:

- §4 lists `collections.notes`.
- §6 gets a "Project notes" bullet, and its "Prompt" and "Context and token counts" bullets
  name the notes block and the `notes` part.
