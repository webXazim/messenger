export type PendingComposerUpload = {
  localId: string;
  file: File;
  fileName: string;
  previewUrl?: string;
  status: "queued" | "uploading" | "uploaded" | "failed";
  uploadId?: string;
  mediaKind?: string;
  width?: number;
  height?: number;
  rotation?: number;
  durationSeconds?: number;
  progress?: number;
  error?: string;
};

export type ComposerUploadResult = {
  uploadId: string;
  mediaKind?: string;
  width?: number;
  height?: number;
  rotation?: number;
  durationSeconds?: number;
};

export type ComposerUploadRequestOptions = {
  signal: AbortSignal;
  onProgress: (progress: number) => void;
};
