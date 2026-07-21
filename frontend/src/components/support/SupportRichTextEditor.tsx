import { useEffect, useMemo, useState } from "react";
import CharacterCount from "@tiptap/extension-character-count";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { Table, TableCell, TableHeader, TableRow } from "@tiptap/extension-table";
import Underline from "@tiptap/extension-underline";
import { EditorContent, useEditor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";

const SAFE_LINK = /^(https:\/\/|mailto:)/i;

function sanitizePastedHtml(html: string) {
  if (typeof DOMParser === "undefined") return html;
  const documentValue = new DOMParser().parseFromString(html, "text/html");
  documentValue.querySelectorAll("script,style,iframe,object,embed,form,input,button").forEach((node) => node.remove());
  documentValue.querySelectorAll("*").forEach((node) => {
    for (const attribute of Array.from(node.attributes)) {
      const name = attribute.name.toLowerCase();
      if (name.startsWith("on") || name === "style" || name === "class" || name === "id") {
        node.removeAttribute(attribute.name);
      }
    }
    if (node instanceof HTMLAnchorElement) {
      const href = node.getAttribute("href")?.trim() ?? "";
      if (!SAFE_LINK.test(href)) {
        node.removeAttribute("href");
        node.removeAttribute("target");
        node.removeAttribute("rel");
      } else {
        node.setAttribute("rel", "noopener noreferrer nofollow");
        node.setAttribute("target", "_blank");
      }
    }
  });
  return documentValue.body.innerHTML;
}

export function plainTextFromHtml(value: string) {
  if (typeof document === "undefined") return value.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
  const wrapper = document.createElement("div");
  wrapper.innerHTML = value;
  return (wrapper.textContent || "").replace(/\s+/g, " ").trim();
}

export function SupportRichTextEditor({
  value,
  onChange,
  direction = "ltr",
}: {
  value: string;
  onChange: (value: string) => void;
  direction?: "ltr" | "rtl";
}) {
  const [expanded, setExpanded] = useState(false);
  const [linkOpen, setLinkOpen] = useState(false);
  const [linkValue, setLinkValue] = useState("");

  const extensions = useMemo(
    () => [
      StarterKit.configure({
        heading: { levels: [2, 3, 4] },
      }),
      Underline,
      Link.configure({
        autolink: true,
        openOnClick: false,
        defaultProtocol: "https",
        protocols: ["https", "mailto"],
        HTMLAttributes: {
          rel: "noopener noreferrer nofollow",
          target: "_blank",
        },
      }),
      Placeholder.configure({
        placeholder:
          "Write the customer-facing answer. Use clear headings, concise paragraphs, numbered steps, tables, or code examples where useful.",
      }),
      CharacterCount,
      Table.configure({ resizable: true }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    [],
  );

  const editor = useEditor({
    extensions,
    content: value || "<p></p>",
    immediatelyRender: false,
    editorProps: {
      attributes: {
        class: "sc-rich-editor__content",
        dir: direction,
        "aria-label": "Article content",
      },
      transformPastedHTML: sanitizePastedHtml,
    },
    onUpdate: ({ editor: currentEditor }) => onChange(currentEditor.getHTML()),
  });

  useEffect(() => {
    if (!editor) return;
    editor.setOptions({
      editorProps: {
        ...editor.options.editorProps,
        attributes: {
          ...editor.options.editorProps.attributes,
          class: "sc-rich-editor__content",
          dir: direction,
          "aria-label": "Article content",
        },
      },
    });
  }, [direction, editor]);

  useEffect(() => {
    if (!editor) return;
    const current = editor.getHTML();
    const next = value || "<p></p>";
    if (current !== next) editor.commands.setContent(next, { emitUpdate: false });
  }, [editor, value]);

  if (!editor) {
    return <div className="sc-rich-editor sc-rich-editor--loading">Loading editor…</div>;
  }

  const textStyle = editor.isActive("heading", { level: 2 })
    ? "h2"
    : editor.isActive("heading", { level: 3 })
      ? "h3"
      : editor.isActive("heading", { level: 4 })
        ? "h4"
        : "p";

  const applyLink = () => {
    const normalized = linkValue.trim();
    if (!SAFE_LINK.test(normalized)) return;
    editor.chain().focus().extendMarkRange("link").setLink({ href: normalized }).run();
    setLinkOpen(false);
    setLinkValue("");
  };

  const openLinkEditor = () => {
    setLinkValue(editor.getAttributes("link").href ?? "https://");
    setLinkOpen(true);
  };

  const wordCount = editor.storage.characterCount.words();
  const characterCount = editor.storage.characterCount.characters();

  return (
    <div className={`sc-rich-editor${expanded ? " is-expanded" : ""}`}>
      <div className="sc-rich-editor__toolbar" role="toolbar" aria-label="Article formatting">
        <select
          aria-label="Text style"
          value={textStyle}
          onChange={(event) => {
            const selected = event.target.value;
            if (selected === "p") editor.chain().focus().setParagraph().run();
            else editor.chain().focus().toggleHeading({ level: Number(selected.slice(1)) as 2 | 3 | 4 }).run();
          }}
        >
          <option value="p">Paragraph</option>
          <option value="h2">Section heading</option>
          <option value="h3">Subheading</option>
          <option value="h4">Small heading</option>
        </select>

        <span className="sc-rich-editor__group">
          <ToolbarButton label="Bold" active={editor.isActive("bold")} onClick={() => editor.chain().focus().toggleBold().run()}>B</ToolbarButton>
          <ToolbarButton label="Italic" active={editor.isActive("italic")} onClick={() => editor.chain().focus().toggleItalic().run()}><em>I</em></ToolbarButton>
          <ToolbarButton label="Underline" active={editor.isActive("underline")} onClick={() => editor.chain().focus().toggleUnderline().run()}><u>U</u></ToolbarButton>
          <ToolbarButton label="Strikethrough" active={editor.isActive("strike")} onClick={() => editor.chain().focus().toggleStrike().run()}><s>S</s></ToolbarButton>
        </span>

        <span className="sc-rich-editor__group">
          <ToolbarButton label="Bulleted list" active={editor.isActive("bulletList")} onClick={() => editor.chain().focus().toggleBulletList().run()}>• List</ToolbarButton>
          <ToolbarButton label="Numbered list" active={editor.isActive("orderedList")} onClick={() => editor.chain().focus().toggleOrderedList().run()}>1. List</ToolbarButton>
          <ToolbarButton label="Quote" active={editor.isActive("blockquote")} onClick={() => editor.chain().focus().toggleBlockquote().run()}>Quote</ToolbarButton>
          <ToolbarButton label="Code block" active={editor.isActive("codeBlock")} onClick={() => editor.chain().focus().toggleCodeBlock().run()}>Code</ToolbarButton>
        </span>

        <span className="sc-rich-editor__group">
          <ToolbarButton label="Add or edit link" active={editor.isActive("link")} onClick={openLinkEditor}>Link</ToolbarButton>
          <ToolbarButton label="Remove link" disabled={!editor.isActive("link")} onClick={() => editor.chain().focus().unsetLink().run()}>Unlink</ToolbarButton>
          <ToolbarButton label="Insert divider" onClick={() => editor.chain().focus().setHorizontalRule().run()}>Divider</ToolbarButton>
          <ToolbarButton label="Insert table" onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>Table</ToolbarButton>
        </span>

        {editor.isActive("table") ? (
          <span className="sc-rich-editor__group sc-rich-editor__group--table">
            <ToolbarButton label="Add table row" onClick={() => editor.chain().focus().addRowAfter().run()}>+ Row</ToolbarButton>
            <ToolbarButton label="Add table column" onClick={() => editor.chain().focus().addColumnAfter().run()}>+ Column</ToolbarButton>
            <ToolbarButton label="Delete table row" onClick={() => editor.chain().focus().deleteRow().run()}>− Row</ToolbarButton>
            <ToolbarButton label="Delete table column" onClick={() => editor.chain().focus().deleteColumn().run()}>− Column</ToolbarButton>
            <ToolbarButton label="Delete table" onClick={() => editor.chain().focus().deleteTable().run()}>Delete table</ToolbarButton>
          </span>
        ) : null}

        <span className="sc-rich-editor__spacer" />
        <span className="sc-rich-editor__group">
          <ToolbarButton label="Undo" disabled={!editor.can().chain().focus().undo().run()} onClick={() => editor.chain().focus().undo().run()}>Undo</ToolbarButton>
          <ToolbarButton label="Redo" disabled={!editor.can().chain().focus().redo().run()} onClick={() => editor.chain().focus().redo().run()}>Redo</ToolbarButton>
          <ToolbarButton label="Clear formatting" onClick={() => editor.chain().focus().unsetAllMarks().clearNodes().run()}>Clear</ToolbarButton>
          <ToolbarButton label={expanded ? "Exit focus mode" : "Open focus mode"} onClick={() => setExpanded((current) => !current)}>{expanded ? "Exit focus" : "Focus mode"}</ToolbarButton>
        </span>
      </div>

      {linkOpen ? (
        <div className="sc-rich-editor__link-panel" role="dialog" aria-label="Edit link">
          <label>
            <span>Secure link</span>
            <input
              autoFocus
              value={linkValue}
              onChange={(event) => setLinkValue(event.target.value)}
              placeholder="https://example.com or mailto:name@example.com"
              onKeyDown={(event) => {
                if (event.key === "Enter") {
                  event.preventDefault();
                  applyLink();
                }
                if (event.key === "Escape") setLinkOpen(false);
              }}
            />
          </label>
          <button type="button" onClick={() => setLinkOpen(false)}>Cancel</button>
          <button type="button" disabled={!SAFE_LINK.test(linkValue.trim())} onClick={applyLink}>Apply link</button>
        </div>
      ) : null}

      <EditorContent editor={editor} />
      <footer className="sc-rich-editor__status">
        <span>{wordCount} words</span>
        <span>{characterCount} characters</span>
        <span>Content is sanitized again by the server before publishing.</span>
      </footer>
    </div>
  );
}

function ToolbarButton({
  label,
  active = false,
  disabled = false,
  onClick,
  children,
}: {
  label: string;
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      className={active ? "is-active" : ""}
      aria-label={label}
      title={label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
