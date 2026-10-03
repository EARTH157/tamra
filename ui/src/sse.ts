/**
 * Incremental Server-Sent Events parser. Feed it decoded text as it arrives; it calls onData
 * with the data of each complete event (several data lines are joined with "\n").
 */
export function createSseParser(onData: (data: string) => void): (chunk: string) => void {
  let buffer = "";
  return (chunk: string) => {
    buffer += chunk;
    let end = buffer.indexOf("\n\n");
    while (end >= 0) {
      const data = buffer
        .slice(0, end)
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).replace(/^ /, ""))
        .join("\n");
      buffer = buffer.slice(end + 2);
      if (data) onData(data);
      end = buffer.indexOf("\n\n");
    }
  };
}
