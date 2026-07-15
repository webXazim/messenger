import { useEffect, useRef, useState } from "react";
import type { PDFDocumentProxy, PDFPageProxy, RenderTask } from "pdfjs-dist";

function PdfPage({ document, pageNumber, availableWidth }: { document: PDFDocumentProxy; pageNumber: number; availableWidth: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [ratio, setRatio] = useState(1.414);

  useEffect(() => {
    if (!availableWidth || !canvasRef.current) return;
    let page: PDFPageProxy | null = null;
    let renderTask: RenderTask | null = null;
    let cancelled = false;

    void document.getPage(pageNumber).then((loadedPage) => {
      if (cancelled || !canvasRef.current) return;
      page = loadedPage;
      const baseViewport = loadedPage.getViewport({ scale: 1 });
      const cssWidth = Math.max(280, availableWidth);
      const cssScale = cssWidth / baseViewport.width;
      const pixelRatio = Math.min(2, window.devicePixelRatio || 1);
      const viewport = loadedPage.getViewport({ scale: cssScale * pixelRatio });
      const canvas = canvasRef.current;
      const context = canvas.getContext("2d", { alpha: false });
      if (!context) return;
      setRatio(baseViewport.height / baseViewport.width);
      canvas.width = Math.max(1, Math.round(viewport.width));
      canvas.height = Math.max(1, Math.round(viewport.height));
      canvas.style.width = `${cssWidth}px`;
      canvas.style.height = `${Math.round(cssWidth * (baseViewport.height / baseViewport.width))}px`;
      context.fillStyle = "#fff";
      context.fillRect(0, 0, canvas.width, canvas.height);
      renderTask = loadedPage.render({ canvas, canvasContext: context, viewport, background: "#fff" });
      return renderTask.promise;
    }).catch((reason: unknown) => {
      if (!cancelled && (reason as { name?: string })?.name !== "RenderingCancelledException") {
        console.warn(`Could not render PDF page ${pageNumber}.`, reason);
      }
    });

    return () => {
      cancelled = true;
      renderTask?.cancel();
      page?.cleanup();
    };
  }, [availableWidth, document, pageNumber]);

  return <canvas ref={canvasRef} className="ms-pdf-document-preview__page" style={{ aspectRatio: `1 / ${ratio}` }} aria-label={`Page ${pageNumber}`} />;
}

export function PdfDocumentPreview({ file, title }: { file: File; title: string }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const [document, setDocument] = useState<PDFDocumentProxy | null>(null);
  const [availableWidth, setAvailableWidth] = useState(0);
  const [error, setError] = useState("");

  useEffect(() => {
    const viewport = viewportRef.current;
    if (!viewport) return;
    const updateWidth = () => setAvailableWidth(Math.max(280, viewport.clientWidth - 32));
    updateWidth();
    const observer = new ResizeObserver(updateWidth);
    observer.observe(viewport);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let destroy: (() => Promise<void>) | null = null;
    setDocument(null);
    setError("");

    void Promise.all([
      import("pdfjs-dist"),
      import("pdfjs-dist/build/pdf.worker.min.mjs?url"),
      file.arrayBuffer(),
    ]).then(async ([{ getDocument, GlobalWorkerOptions }, workerModule, bytes]) => {
      GlobalWorkerOptions.workerSrc = workerModule.default;
      const loadingTask = getDocument({ data: new Uint8Array(bytes) });
      destroy = () => loadingTask.destroy();
      const loadedDocument = await loadingTask.promise;
      if (cancelled) {
        await loadingTask.destroy();
        return;
      }
      setDocument(loadedDocument);
    }).catch((reason: unknown) => {
      if (!cancelled) setError(reason instanceof Error ? reason.message : "PDF preview is unavailable.");
    });

    return () => {
      cancelled = true;
      if (destroy) void destroy();
    };
  }, [file]);

  return (
    <div ref={viewportRef} className="ms-pdf-document-preview" role="document" aria-label={`Preview ${title}`}>
      {error ? <div className="ms-pdf-document-preview__state">{error}</div> : null}
      {!error && !document ? <div className="ms-pdf-document-preview__state">Preparing document…</div> : null}
      {document && availableWidth ? (
        <div className="ms-pdf-document-preview__pages">
          {Array.from({ length: document.numPages }, (_, index) => (
            <PdfPage key={index + 1} document={document} pageNumber={index + 1} availableWidth={availableWidth} />
          ))}
        </div>
      ) : null}
    </div>
  );
}
