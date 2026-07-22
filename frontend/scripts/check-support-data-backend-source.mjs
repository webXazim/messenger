import fs from "node:fs";

function requireText(file, text) {
  const source = fs.readFileSync(file, "utf8");
  if (!source.includes(text)) throw new Error(`Missing ${JSON.stringify(text)} in ${file}`);
}

requireText("frontend/src/lib/config.ts", 'VITE_SUPPORT_DATA_BACKEND');
requireText("frontend/src/lib/config.ts", 'SUPPORT_DATA_URL = "/support-fast"');
requireText("frontend/src/api/support.ts", 'supportDataPath("/conversations/")');
requireText("frontend/src/api/support.ts", 'supportDataPath(`/conversations/${conversationId}/messages/`)');
requireText("frontend/src/api/support.ts", 'supportDataPath(`/conversations/${conversationId}/claim/`)');
requireText("frontend/src/api/support.ts", 'supportDataPath(`/calls/${callId}/signals/`)');
requireText("frontend/src/api/support.ts", '"/support/calls/turn-credentials/"');
requireText("frontend/src/api/support.ts", '`/support/conversations/${conversationId}/uploads/`');
requireText("frontend/public/support-widget/v1/widget.js", 'config.data_plane_backend === "axum"');
requireText("frontend/public/support-widget/v1/widget.js", 'dataSessionPath("/messages/")');
requireText("frontend/public/support-widget/v1/widget.js", 'uploadRequest(sessionPath("/conversation/uploads/")');
console.log("Support data-plane frontend source contracts passed.");
