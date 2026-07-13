export type PendingComposerUpload = {
  localId: string;
  file: File;
  fileName: string;
  previewUrl?: string;
  status: "queued" | "uploading" | "uploaded" | "failed";
  uploadId?: string;
  progress?: number;
  error?: string;
};

export type ComposerUploadRequestOptions = {
  signal: AbortSignal;
  onProgress: (progress: number) => void;
};
