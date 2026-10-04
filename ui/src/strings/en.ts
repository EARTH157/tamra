// English UI strings, the reference table: th.ts must have exactly the same keys.
// Placeholders look like {name}. A key with a ".one" twin uses it when count is 1.
// Brand names (Tamra, ตำรา) and model names are not translated.
export const en = {
  "common.cancel": "Cancel",
  "common.close": "Close",
  "common.dismiss": "Dismiss",
  "common.retry": "Retry",

  "app.newChat": "New chat",
  "app.chats": "Chats",
  "app.noChatsNoFolder": "No chats yet. Add a folder of documents to start.",
  "app.noChatsYet": "No chats yet. Ask a question to start one.",
  "app.coreUnreachable": "Tamra core is unreachable. Answers are paused until it reconnects.",
  "app.connecting": "Connecting to Tamra…",

  "folder.changeTitle": "Change documents folder",
  "folder.chooseTitle": "Choose documents folder",
  "folder.changeText":
    "Tamra will index the new folder and answer from it. Your files are never changed.",
  "folder.chooseText":
    "Tamra indexes the PDF, Word, text, and Markdown files in this folder and its subfolders. Your files are never changed.",
  "folder.pathLabel": "Folder path",
  "folder.pathPlaceholder": "Full folder path, like D:\Work\Documents",
  "folder.browse": "Browse…",
  "folder.use": "Use this folder",

  "welcome.title": "Welcome to Tamra",
  "welcome.subtitle":
    "Ask questions about your own documents and check every answer against its source.",
  "welcome.addTitle": "Add a folder of documents",
  "welcome.addText": "Choose a folder of PDF, Word, text, or Markdown files.",
  "welcome.choose": "Choose a folder",
  "welcome.offline": "Works offline",
  "welcome.cites": "Answers cite their source",
  "welcome.languages": "Thai, English, and Chinese",

  "deleteChat.title": "Delete this chat?",
  "deleteChat.text":
    "\"{title}\" will be removed from this computer. Your documents are not changed.",
  "deleteChat.confirm": "Delete chat",

  "chatList.untitled": "New chat",
  "chatList.options": "Options for {title}",
  "chatList.menu": "Chat options",
  "chatList.rename": "Rename",
  "chatList.renameLabel": "Chat title",
  "chatList.renameHint": "Enter to save · Esc to cancel",

  "chat.defaultCollection": "your documents",
  "chat.emptyTitle": "Ask your documents",
  "chat.emptyText": "Every answer cites the passage it came from, so you can check it.",
  "chat.searching": "Searching your documents…",
  "chat.questionLabel": "Question",
  "chat.placeholder": "Ask about your documents…",
  "chat.send": "Send",
  "chat.stop": "Stop",
  "chat.composerHint": "Enter to send · Shift+Enter for a new line",
  "chat.notFoundTitle": "Not found in {name}",
  "chat.notFoundText": "Tamra answers only from your documents, so it will not guess.",
  "chat.citeHint": "Click a number to see the passage it came from.",
  "chat.cite": "Source {n}",
  "chat.sourcePanel": "Source",
  "chat.closeSource": "Close source",
  "chat.streamEmpty": "The answer stream is empty.",

  "index.cardLabel": "Documents folder",
  "index.changeFolder": "Change folder",
  "index.rebuild": "Rebuild index",
  "index.stale":
    "This index was built with a different embedding model. Rebuild it to search again.",
  "index.checking": "Checking files…",
  "index.none": "No documents found yet",
  "index.progress": "Indexing progress",
  "index.partial": "{indexed} of {total} files indexed",
  "index.filesIndexed": "{count} files indexed",
  "index.filesIndexed.one": "1 file indexed",
  "index.current": "Indexing {file}",
  "index.needAttention": "{count} files need attention",
  "index.needAttention.one": "1 file needs attention",
  "index.failed": "Failed: {error}",
  "index.skipped": "Skipped: {error}",
  "index.failedBare": "Failed",
  "index.skippedBare": "Skipped",
} as const;

export type TranslationKey = keyof typeof en;
