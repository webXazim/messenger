import PdfWorker from "pdfjs-dist/build/pdf.worker.min.mjs?worker&inline";

export function createPdfWorker() {
  return new PdfWorker();
}
