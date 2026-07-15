import type * as PdfJs from "pdfjs-dist";

let worker: Worker | null = null;

export async function loadPdfRuntime(): Promise<typeof PdfJs> {
  const [pdfjs, workerFactory] = await Promise.all([
    import("pdfjs-dist"),
    import("./pdfWorkerFactory"),
  ]);
  if (!worker) worker = workerFactory.createPdfWorker();
  pdfjs.GlobalWorkerOptions.workerPort = worker;
  return pdfjs;
}
