import { useEffect, useRef } from "react";

const COMMANDS = [
  ["bold", "Bold"],
  ["italic", "Italic"],
  ["underline", "Underline"],
  ["insertUnorderedList", "Bulleted list"],
  ["insertOrderedList", "Numbered list"],
  ["formatBlock", "Quote", "blockquote"],
] as const;

export function plainTextFromHtml(value: string) {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = value;
  return (wrapper.textContent || "").replace(/\s+/g, " ").trim();
}

export function SupportRichTextEditor({ value, onChange, direction = "ltr" }: { value: string; onChange: (value: string) => void; direction?: "ltr" | "rtl" }) {
  const editorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const editor = editorRef.current;
    if (editor && editor.innerHTML !== value) editor.innerHTML = value;
  }, [value]);

  const run = (command: string, commandValue?: string) => {
    editorRef.current?.focus();
    document.execCommand(command, false, commandValue);
    onChange(editorRef.current?.innerHTML || "");
  };

  const addLink = () => {
    const href = window.prompt("Enter a secure link beginning with https:// or mailto:");
    if (!href) return;
    const normalized = href.trim();
    if (!/^(https:\/\/|mailto:)/i.test(normalized)) {
      window.alert("Use an HTTPS or mailto link.");
      return;
    }
    run("createLink", normalized);
  };

  return (
    <div className="sc-rich-editor">
      <div className="sc-rich-editor__toolbar" role="toolbar" aria-label="Article formatting">
        <select aria-label="Text style" defaultValue="p" onChange={(event) => run("formatBlock", event.target.value)}>
          <option value="p">Paragraph</option>
          <option value="h2">Section heading</option>
          <option value="h3">Subheading</option>
          <option value="h4">Small heading</option>
        </select>
        {COMMANDS.map(([command, label, commandValue]) => (
          <button key={label} type="button" onClick={() => run(command, commandValue)} title={label} aria-label={label}>
            {label === "Bold" ? "B" : label === "Italic" ? "I" : label === "Underline" ? "U" : label === "Bulleted list" ? "• List" : label === "Numbered list" ? "1. List" : "Quote"}
          </button>
        ))}
        <button type="button" onClick={addLink}>Link</button>
        <button type="button" onClick={() => run("unlink")}>Remove link</button>
        <span className="sc-rich-editor__spacer" />
        <button type="button" onClick={() => run("undo")}>Undo</button>
        <button type="button" onClick={() => run("redo")}>Redo</button>
        <button type="button" onClick={() => run("removeFormat")}>Clear formatting</button>
      </div>
      <div
        ref={editorRef}
        className="sc-rich-editor__content"
        contentEditable
        role="textbox"
        aria-multiline="true"
        aria-label="Article content"
        dir={direction}
        data-placeholder="Write the customer-facing answer here. Use headings, short paragraphs, and clear steps."
        onInput={() => onChange(editorRef.current?.innerHTML || "")}
        onPaste={(event) => {
          event.preventDefault();
          const text = event.clipboardData.getData("text/plain");
          document.execCommand("insertText", false, text);
        }}
      />
    </div>
  );
}
