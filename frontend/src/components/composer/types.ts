export type PendingComposerUpload = {
  localId: string;
  file: File;
  fileName: string;
  previewUrl?: string;
  thumbnailUrl?: string;
  status: "queued" | "uploading" | "uploaded" | "failed";
  uploadId?: string;
  mediaKind?: string;
  width?: number;
  height?: number;
  rotation?: number;
  durationSeconds?: number;
  progress?: number;
  error?: string;
  viewOnce?: boolean;
};

export type ComposerUploadResult = {
  uploadId: string;
  mediaKind?: string;
  width?: number;
  height?: number;
  rotation?: number;
  durationSeconds?: number;
  thumbnailBlob?: Blob | null;
};

export type ComposerUploadRequestOptions = {
  signal: AbortSignal;
  onProgress: (progress: number) => void;
};
