import { Camera, Loader2, RefreshCw } from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Button } from "./ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "./ui/dialog";
import {
  ETIQUETAS_FORMATO,
  SesionEscanerCamara,
  type DetectorCodigo,
  type EntornoEscanerCamara,
  type EstadoEscanerCamara,
  type FlujoCamara,
  type VideoCamara,
} from "./CameraBarcodeScanner.helpers";

interface BarcodeDetectorConstructor {
  new (options: { formats: string[] }): DetectorCodigo;
  getSupportedFormats(): Promise<string[]>;
}

type WindowConDetector = Window &
  typeof globalThis & {
    BarcodeDetector?: BarcodeDetectorConstructor;
  };

const ESTADO_VISUAL_INICIAL: EstadoEscanerCamara = {
  phase: "idle",
  cameras: [],
  selectedDeviceId: "",
  supportedFormats: [],
  unavailableFormats: [],
  detectedCode: null,
  error: null,
  engine: null,
};

const crearEntornoNavegador = (): EntornoEscanerCamara => {
  const browserWindow = window as WindowConDetector;
  const Detector = browserWindow.BarcodeDetector;
  return {
    secureContext: browserWindow.isSecureContext,
    getSupportedFormats: Detector
      ? () => Detector.getSupportedFormats()
      : undefined,
    createDetector: Detector
      ? (formats) => new Detector({ formats })
      : undefined,
    loadAlternativeDetector: async () => {
      const { crearDetectorZXing } = await import(
        "./CameraBarcodeScanner.zxing"
      );
      return crearDetectorZXing();
    },
    getUserMedia: navigator.mediaDevices?.getUserMedia
      ? (constraints) =>
          navigator.mediaDevices.getUserMedia(
            constraints as MediaStreamConstraints,
          ) as unknown as Promise<FlujoCamara>
      : undefined,
    enumerateCameras: navigator.mediaDevices?.enumerateDevices
      ? async () =>
          (await navigator.mediaDevices.enumerateDevices())
            .filter((device) => device.kind === "videoinput")
            .map((device, index) => ({
              deviceId: device.deviceId,
              label: device.label || `Cámara ${index + 1}`,
            }))
      : undefined,
    requestFrame: (callback) => window.requestAnimationFrame(callback),
    cancelFrame: (id) => window.cancelAnimationFrame(id),
  };
};

interface CameraBarcodeScannerProps {
  onDetected: (code: string) => void | Promise<void>;
  disabled?: boolean;
  className?: string;
}

export function CameraBarcodeScanner({
  onDetected,
  disabled = false,
  className,
}: CameraBarcodeScannerProps) {
  const [open, setOpen] = useState(false);
  const [scannerState, setScannerState] = useState(ESTADO_VISUAL_INICIAL);
  const cameraSelectId = useId();
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const scannerRef = useRef<SesionEscanerCamara | null>(null);
  const onDetectedRef = useRef(onDetected);
  const startRequestedRef = useRef(false);
  const mountedRef = useRef(true);

  onDetectedRef.current = onDetected;

  if (!scannerRef.current && typeof window !== "undefined") {
    scannerRef.current = new SesionEscanerCamara(crearEntornoNavegador(), {
      onStateChange: (state) => {
        if (mountedRef.current) setScannerState(state);
      },
      onDetected: (code) => {
        if (!mountedRef.current) return;
        void Promise.resolve(onDetectedRef.current(code)).catch(() => undefined);
      },
    });
  }

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      scannerRef.current?.close();
    };
  }, []);

  const handleVideoRef = useCallback(
    (video: HTMLVideoElement | null) => {
      videoRef.current = video;
      if (!video || !open || !startRequestedRef.current) return;
      startRequestedRef.current = false;
      void scannerRef.current?.open(video as unknown as VideoCamara);
    },
    [open],
  );

  const openScanner = () => {
    startRequestedRef.current = true;
    setScannerState(ESTADO_VISUAL_INICIAL);
    setOpen(true);
  };

  const closeScanner = () => {
    startRequestedRef.current = false;
    scannerRef.current?.close();
    setOpen(false);
  };

  const restartScanner = (deviceId = scannerState.selectedDeviceId) => {
    if (!videoRef.current) return;
    void scannerRef.current?.open(
      videoRef.current as unknown as VideoCamara,
      deviceId,
    );
  };

  const supportedText = scannerState.supportedFormats
    .map((format) => ETIQUETAS_FORMATO[format])
    .join(", ");
  const unavailableText = scannerState.unavailableFormats
    .map((format) => ETIQUETAS_FORMATO[format])
    .join(", ");

  return (
    <>
      <Button
        type="button"
        variant="outline"
        onClick={openScanner}
        disabled={disabled}
        className={className}
      >
        <Camera className="mr-2 size-4" aria-hidden="true" />
        Escanear con cámara
      </Button>

      <Dialog
        open={open}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) closeScanner();
        }}
      >
        <DialogContent className="sm:max-w-xl">
          <DialogHeader>
            <DialogTitle>Escanear código con cámara</DialogTitle>
            <DialogDescription>
              Coloca un solo código dentro de la imagen y mantenlo estable. La
              lectura no guarda productos ni confirma operaciones.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="relative aspect-video overflow-hidden rounded-lg bg-black">
              <video
                ref={handleVideoRef}
                muted
                playsInline
                aria-label="Vista previa de la cámara para leer código"
                className={
                  "size-full object-cover " +
                  (scannerState.phase === "scanning" ? "block" : "invisible")
                }
              />
              {scannerState.phase !== "scanning" && (
                <div className="absolute inset-0 flex items-center justify-center p-6 text-center text-sm text-white">
                  {scannerState.phase === "opening" && (
                    <span className="flex items-center gap-2">
                      <Loader2
                        className="size-5 animate-spin"
                        aria-hidden="true"
                      />
                      Preparando la cámara…
                    </span>
                  )}
                  {scannerState.phase === "detected" && (
                    <span>
                      Código detectado:{" "}
                      <strong className="font-mono">
                        {scannerState.detectedCode}
                      </strong>
                    </span>
                  )}
                  {scannerState.phase === "error" && (
                    <span>No hay una vista de cámara disponible.</span>
                  )}
                </div>
              )}
            </div>

            {scannerState.cameras.length > 1 &&
              scannerState.phase === "scanning" && (
                <div className="space-y-1">
                  <label
                    htmlFor={cameraSelectId}
                    className="text-sm font-medium"
                  >
                    Cámara
                  </label>
                  <select
                    id={cameraSelectId}
                    value={scannerState.selectedDeviceId}
                    onChange={(event) => restartScanner(event.target.value)}
                    className="border-input bg-background h-10 w-full rounded-md border px-3 text-sm"
                  >
                    {scannerState.cameras.map((camera) => (
                      <option key={camera.deviceId} value={camera.deviceId}>
                        {camera.label}
                      </option>
                    ))}
                  </select>
                </div>
              )}

            {scannerState.phase === "scanning" && (
              <div role="status" className="text-sm text-muted-foreground">
                Buscando un código con {scannerState.engine === "native"
                  ? "el detector del navegador"
                  : "el lector compatible ZXing"}. Formatos disponibles: {supportedText}.
              </div>
            )}
            {scannerState.unavailableFormats.length > 0 &&
              scannerState.supportedFormats.length > 0 && (
                <p className="text-xs text-amber-700 dark:text-amber-400">
                  Este dispositivo no ofrece por cámara: {unavailableText}.
                </p>
              )}
            {scannerState.error && (
              <div
                role="alert"
                className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950/30 dark:text-amber-200"
              >
                {scannerState.error}
              </div>
            )}
          </div>

          <DialogFooter className="gap-2 sm:gap-0">
            <Button type="button" variant="outline" onClick={closeScanner}>
              Cancelar
            </Button>
            {scannerState.phase === "detected" && (
              <Button type="button" onClick={() => restartScanner()}>
                <RefreshCw className="mr-2 size-4" aria-hidden="true" />
                Escanear otro
              </Button>
            )}
            {scannerState.phase === "error" && (
              <Button type="button" onClick={() => restartScanner()}>
                Reintentar
              </Button>
            )}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
