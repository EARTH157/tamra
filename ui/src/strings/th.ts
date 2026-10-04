// Thai UI strings. Same keys as en.ts (checked by the compiler and by i18n.test.ts).
// Thai has no plural forms, so a ".one" key repeats the base wording.
import type { TranslationKey } from "./en";

export const th: Record<TranslationKey, string> = {
  "common.cancel": "ยกเลิก",
  "common.close": "ปิด",
  "common.dismiss": "ปิด",
  "common.retry": "ลองอีกครั้ง",

  "app.newChat": "แชทใหม่",
  "app.chats": "แชท",
  "app.noChatsNoFolder": "ยังไม่มีแชท เพิ่มโฟลเดอร์เอกสารเพื่อเริ่มต้นใช้งาน",
  "app.noChatsYet": "ยังไม่มีแชท ลองถามคำถามเพื่อเริ่มแชทแรก",
  "app.coreUnreachable":
    "เชื่อมต่อระบบหลักของ Tamra ไม่ได้ การตอบคำถามจะหยุดไว้ก่อนจนกว่าจะเชื่อมต่อได้อีกครั้ง",
  "app.connecting": "กำลังเชื่อมต่อ Tamra…",

  "folder.changeTitle": "เปลี่ยนโฟลเดอร์เอกสาร",
  "folder.chooseTitle": "เลือกโฟลเดอร์เอกสาร",
  "folder.changeText":
    "Tamra จะสร้างดัชนีของโฟลเดอร์ใหม่และตอบจากโฟลเดอร์นี้ ไฟล์ของคุณจะไม่ถูกแก้ไข",
  "folder.chooseText":
    "Tamra จะสร้างดัชนีของไฟล์ PDF, Word, ข้อความ และ Markdown ในโฟลเดอร์นี้และโฟลเดอร์ย่อยทั้งหมด ไฟล์ของคุณจะไม่ถูกแก้ไข",
  "folder.pathLabel": "ที่อยู่โฟลเดอร์",
  "folder.pathPlaceholder": "ใส่ที่อยู่โฟลเดอร์แบบเต็ม เช่น D:\Work\Documents",
  "folder.browse": "เลือกโฟลเดอร์…",
  "folder.use": "ใช้โฟลเดอร์นี้",

  "welcome.title": "ยินดีต้อนรับสู่ Tamra",
  "welcome.subtitle": "ถามคำถามจากเอกสารของคุณเอง แล้วตรวจคำตอบกับต้นฉบับได้ทุกข้อ",
  "welcome.addTitle": "เพิ่มโฟลเดอร์เอกสาร",
  "welcome.addText": "เลือกโฟลเดอร์ที่มีไฟล์ PDF, Word, ข้อความ หรือ Markdown",
  "welcome.choose": "เลือกโฟลเดอร์",
  "welcome.offline": "ใช้งานได้โดยไม่ต้องออนไลน์",
  "welcome.cites": "คำตอบอ้างอิงแหล่งที่มา",
  "welcome.languages": "รองรับไทย อังกฤษ และจีน",

  "deleteChat.title": "ลบแชทนี้ไหม",
  "deleteChat.text": "“{title}” จะถูกลบออกจากคอมพิวเตอร์เครื่องนี้ เอกสารของคุณจะไม่ถูกแก้ไข",
  "deleteChat.confirm": "ลบแชท",

  "chatList.untitled": "แชทใหม่",
  "chatList.options": "ตัวเลือกของ {title}",
  "chatList.menu": "ตัวเลือกแชท",
  "chatList.rename": "เปลี่ยนชื่อ",
  "chatList.renameLabel": "ชื่อแชท",
  "chatList.renameHint": "กด Enter เพื่อบันทึก · กด Esc เพื่อยกเลิก",

  "chat.defaultCollection": "เอกสารของคุณ",
  "chat.emptyTitle": "ถามจากเอกสารของคุณ",
  "chat.emptyText": "ทุกคำตอบจะอ้างอิงข้อความต้นฉบับที่ใช้ตอบ คุณจึงตรวจสอบได้เอง",
  "chat.searching": "กำลังค้นหาในเอกสารของคุณ…",
  "chat.questionLabel": "คำถาม",
  "chat.placeholder": "ถามเกี่ยวกับเอกสารของคุณ…",
  "chat.send": "ส่ง",
  "chat.stop": "หยุด",
  "chat.composerHint": "กด Enter เพื่อส่ง · กด Shift+Enter เพื่อขึ้นบรรทัดใหม่",
  "chat.notFoundTitle": "ไม่พบใน {name}",
  "chat.notFoundText": "Tamra ตอบจากเอกสารของคุณเท่านั้น จึงไม่เดาคำตอบเอง",
  "chat.citeHint": "คลิกตัวเลขเพื่อดูข้อความต้นฉบับที่ใช้ตอบ",
  "chat.cite": "แหล่งที่มา {n}",
  "chat.sourcePanel": "แหล่งที่มา",
  "chat.closeSource": "ปิดแหล่งที่มา",
  "chat.streamEmpty": "ไม่ได้รับข้อมูลคำตอบ",

  "index.cardLabel": "โฟลเดอร์เอกสาร",
  "index.changeFolder": "เปลี่ยนโฟลเดอร์",
  "index.rebuild": "สร้างดัชนีใหม่",
  "index.stale":
    "ดัชนีนี้สร้างด้วยโมเดลฝังข้อความตัวอื่น ต้องสร้างใหม่จึงจะค้นหาได้อีกครั้ง",
  "index.checking": "กำลังตรวจสอบไฟล์…",
  "index.none": "ยังไม่พบเอกสาร",
  "index.progress": "ความคืบหน้าการสร้างดัชนี",
  "index.partial": "สร้างดัชนีแล้ว {indexed} จาก {total} ไฟล์",
  "index.filesIndexed": "สร้างดัชนีแล้ว {count} ไฟล์",
  "index.filesIndexed.one": "สร้างดัชนีแล้ว 1 ไฟล์",
  "index.current": "กำลังสร้างดัชนี {file}",
  "index.needAttention": "{count} ไฟล์ต้องตรวจสอบ",
  "index.needAttention.one": "1 ไฟล์ต้องตรวจสอบ",
  "index.failed": "ล้มเหลว: {error}",
  "index.skipped": "ข้ามไฟล์: {error}",
  "index.failedBare": "ล้มเหลว",
  "index.skippedBare": "ข้ามไฟล์",
};
