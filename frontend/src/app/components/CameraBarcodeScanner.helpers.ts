import { normalizarEntradaCodigo } from "../pages/CodigosProducto.helpers.ts";

export const FORMATOS_CODIGO_OBJETIVO = [
  "ean_13",
  "ean_8",
  "upc_a",
  "code_128",
] as const;

export type FormatoCodigoObjetivo = (typeof FORMATOS_CODIGO_OBJETIVO)[number];
export type MotorEscaner = "native" | "zxing";

export const ETIQUETAS_FORMATO: Record<FormatoCodigoObjetivo, string> = {
  ean_13: "EAN-13",
  ean_8: "EAN-8",
  upc_a: "UPC-A",
  code_128: "Code 128",
};

export interface CamaraDisponible {
  deviceId: string;
  label: string;
}

export interface ResultadoDeteccion {
  rawValue: string;
  format?: string;
}

export interface DetectorCodigo {
  detect(source: unknown): Promise<ResultadoDeteccion[]>;
}

export interface TrackCamara {
  stop(): void;
  getSettings?(): { deviceId?: string };
}

export interface FlujoCamara {
  getTracks(): TrackCamara[];
  getVideoTracks?(): TrackCamara[];
}

export interface VideoCamara {
  srcObject: FlujoCamara | null;
  play(): Promise<void>;
}

export interface ControlDetectorAlternativo {
  stop(): void;
}

export interface DetectorAlternativo {
  start(
    stream: FlujoCamara,
    video: VideoCamara,
    onDetected: (result: ResultadoDeteccion) => void,
  ): Promise<ControlDetectorAlternativo>;
}

export interface EntornoEscanerCamara {
  secureContext: boolean;
  getSupportedFormats?: () => Promise<string[]>;
  createDetector?: (formats: string[]) => DetectorCodigo;
  loadAlternativeDetector?: () => Promise<DetectorAlternativo>;
  getUserMedia?: (constraints: unknown) => Promise<FlujoCamara>;
  enumerateCameras?: () => Promise<CamaraDisponible[]>;
  requestFrame: (callback: () => void) => number;
  cancelFrame: (id: number) => void;
}

export type FaseEscaner =
  | "idle"
  | "opening"
  | "scanning"
  | "detected"
  | "error";

export interface EstadoEscanerCamara {
  phase: FaseEscaner;
  cameras: CamaraDisponible[];
  selectedDeviceId: string;
  supportedFormats: FormatoCodigoObjetivo[];
  unavailableFormats: FormatoCodigoObjetivo[];
  detectedCode: string | null;
  error: string | null;
  engine: MotorEscaner | null;
}

interface CallbacksEscanerCamara {
  onStateChange: (state: EstadoEscanerCamara) => void;
  onDetected: (code: string, format?: string) => void;
}

const ESTADO_INICIAL: EstadoEscanerCamara = {
  phase: "idle",
  cameras: [],
  selectedDeviceId: "",
  supportedFormats: [],
  unavailableFormats: [...FORMATOS_CODIGO_OBJETIVO],
  detectedCode: null,
  error: null,
  engine: null,
};

const crearError = (name: string) => Object.assign(new Error(name), { name });

const mensajeErrorCamara = (error: unknown): string => {
  const name = (error as { name?: string })?.name;
  if (name === "NotAllowedError" || name === "SecurityError") {
    return "No se concedió acceso a la cámara. Puedes habilitarlo en los permisos del navegador o usar la entrada manual.";
  }
  if (name === "NotFoundError" || name === "DevicesNotFoundError") {
    return "No se encontró una cámara disponible. Puedes usar la entrada manual o un lector en modo teclado.";
  }
  if (name === "NotReadableError" || name === "TrackStartError") {
    return "La cámara está ocupada o no pudo iniciarse. Cierra otras aplicaciones que la usen e inténtalo de nuevo.";
  }
  if (
    name === "OverconstrainedError" ||
    name === "ConstraintNotSatisfiedError"
  ) {
    return "La cámara seleccionada ya no está disponible. Elige otra cámara o vuelve a intentarlo.";
  }
  if (name === "AbortError") {
    return "La apertura de la cámara fue interrumpida. Vuelve a intentarlo.";
  }
  return "No se pudo usar la cámara para leer el código. Continúa con la entrada manual o el lector en modo teclado.";
};

export class SesionEscanerCamara {
  private readonly environment: EntornoEscanerCamara;
  private readonly callbacks: CallbacksEscanerCamara;
  private generation = 0;
  private active = false;
  private detectionLocked = false;
  private detectionInFlight = false;
  private frameId: number | null = null;
  private stream: FlujoCamara | null = null;
  private video: VideoCamara | null = null;
  private detector: DetectorCodigo | null = null;
  private alternativeControl: ControlDetectorAlternativo | null = null;
  private state: EstadoEscanerCamara = { ...ESTADO_INICIAL };

  constructor(
    environment: EntornoEscanerCamara,
    callbacks: CallbacksEscanerCamara,
  ) {
    this.environment = environment;
    this.callbacks = callbacks;
  }

  getState(): EstadoEscanerCamara {
    return {
      ...this.state,
      cameras: [...this.state.cameras],
      supportedFormats: [...this.state.supportedFormats],
      unavailableFormats: [...this.state.unavailableFormats],
    };
  }

  async open(video: VideoCamara, deviceId = ""): Promise<void> {
    this.stopCapture();
    const generation = ++this.generation;
    this.active = true;
    this.detectionLocked = false;
    this.detectionInFlight = false;
    this.video = video;
    this.updateState({
      phase: "opening",
      selectedDeviceId: deviceId,
      detectedCode: null,
      error: null,
      engine: null,
    });

    try {
      if (!this.environment.secureContext) {
        throw crearError("InsecureContextError");
      }
      if (!this.environment.getUserMedia) {
        throw crearError("CameraApiUnavailableError");
      }

      const engine = await this.selectEngine(generation);
      if (!engine || !this.isCurrent(generation)) return;

      const constraints = {
        audio: false,
        video: deviceId
          ? { deviceId: { exact: deviceId } }
          : {
              facingMode: { ideal: "environment" },
              width: { ideal: 1280 },
              height: { ideal: 720 },
            },
      };
      const stream = await this.environment.getUserMedia(constraints);
      if (!this.isCurrent(generation)) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      this.stream = stream;
      video.srcObject = stream;
      await video.play();
      if (!this.isCurrent(generation)) {
        this.stopCapture();
        return;
      }

      let cameras: CamaraDisponible[] = [];
      if (this.environment.enumerateCameras) {
        try {
          cameras = await this.environment.enumerateCameras();
        } catch {
          cameras = [];
        }
      }
      if (!this.isCurrent(generation)) return;

      const activeDeviceId =
        deviceId ||
        stream.getVideoTracks?.()[0]?.getSettings?.().deviceId ||
        "";
      this.updateState({
        phase: "scanning",
        cameras,
        selectedDeviceId: activeDeviceId,
        supportedFormats: [...FORMATOS_CODIGO_OBJETIVO],
        unavailableFormats: [],
        engine: engine.kind,
      });

      if (engine.kind === "native") {
        this.scheduleDetection(generation);
      } else {
        const control = await engine.detector.start(
          stream,
          video,
          (result) => this.handleAlternativeDetection(generation, result),
        );
        if (!this.isCurrent(generation) || this.detectionLocked) {
          control.stop();
          return;
        }
        this.alternativeControl = control;
      }
    } catch (error) {
      if (!this.isCurrent(generation)) return;
      this.active = false;
      this.stopCapture();
      const name = (error as { name?: string })?.name;
      let message = mensajeErrorCamara(error);
      if (name === "InsecureContextError") {
        message = "La cámara web requiere HTTPS. En este equipo también funciona desde localhost; desde un celular usa una dirección HTTPS.";
      } else if (name === "CameraApiUnavailableError") {
        message = "Este navegador o dispositivo no ofrece acceso a una cámara. Usa la entrada manual o un lector en modo teclado.";
      } else if (name === "AlternativeDetectorUnavailableError") {
        message = "Este navegador no ofrece el detector nativo requerido y no hay un lector alternativo disponible. Usa la entrada manual o un lector en modo teclado.";
      } else if (name === "AlternativeDetectorLoadError") {
        message = "No se pudo cargar el lector de códigos compatible. Recarga la página o usa la entrada manual o un lector en modo teclado.";
      }
      this.updateState({ phase: "error", error: message });
    }
  }

  close(): void {
    this.active = false;
    this.detectionLocked = false;
    this.detectionInFlight = false;
    this.generation += 1;
    this.stopCapture();
    this.updateState({
      phase: "idle",
      detectedCode: null,
      error: null,
      engine: null,
    });
  }

  private async selectEngine(generation: number): Promise<
    | { kind: "native" }
    | { kind: "zxing"; detector: DetectorAlternativo }
    | null
  > {
    if (
      this.environment.getSupportedFormats &&
      this.environment.createDetector
    ) {
      try {
        const reportedFormats =
          await this.environment.getSupportedFormats();
        if (!this.isCurrent(generation)) return null;
        if (
          FORMATOS_CODIGO_OBJETIVO.every((format) =>
            reportedFormats.includes(format),
          )
        ) {
          this.detector = this.environment.createDetector([
            ...FORMATOS_CODIGO_OBJETIVO,
          ]);
          return { kind: "native" };
        }
      } catch {
        // Un detector nativo defectuoso no impide intentar el lector local.
      }
    }

    if (!this.environment.loadAlternativeDetector) {
      throw crearError("AlternativeDetectorUnavailableError");
    }
    try {
      const detector = await this.environment.loadAlternativeDetector();
      if (!this.isCurrent(generation)) return null;
      return { kind: "zxing", detector };
    } catch {
      throw crearError("AlternativeDetectorLoadError");
    }
  }

  private isCurrent(generation: number): boolean {
    return this.active && generation === this.generation;
  }

  private scheduleDetection(generation: number): void {
    if (!this.isCurrent(generation) || this.detectionLocked) return;
    this.frameId = this.environment.requestFrame(() => {
      this.frameId = null;
      void this.detect(generation);
    });
  }

  private async detect(generation: number): Promise<void> {
    if (
      !this.isCurrent(generation) ||
      this.detectionLocked ||
      this.detectionInFlight ||
      !this.detector ||
      !this.video
    ) {
      return;
    }

    this.detectionInFlight = true;
    try {
      const results = await this.detector.detect(this.video);
      if (!this.isCurrent(generation) || this.detectionLocked) return;

      const detected = results.find((result) =>
        normalizarEntradaCodigo(result.rawValue),
      );
      const code = detected
        ? normalizarEntradaCodigo(detected.rawValue)
        : null;
      if (!code) {
        this.scheduleDetection(generation);
        return;
      }

      this.detectionLocked = true;
      this.active = false;
      this.stopCapture();
      this.updateState({
        phase: "detected",
        detectedCode: code,
        error: null,
      });
      this.callbacks.onDetected(code, detected?.format);
    } catch (error) {
      if (!this.isCurrent(generation)) return;
      this.active = false;
      this.stopCapture();
      this.updateState({ phase: "error", error: mensajeErrorCamara(error) });
    } finally {
      this.detectionInFlight = false;
    }
  }

  private handleAlternativeDetection(
    generation: number,
    result: ResultadoDeteccion,
  ): void {
    if (!this.isCurrent(generation) || this.detectionLocked) return;
    const code = normalizarEntradaCodigo(result.rawValue);
    if (!code) return;

    this.detectionLocked = true;
    this.active = false;
    this.stopCapture();
    this.updateState({
      phase: "detected",
      detectedCode: code,
      error: null,
    });
    this.callbacks.onDetected(code, result.format);
  }

  private stopCapture(): void {
    if (this.frameId !== null) {
      this.environment.cancelFrame(this.frameId);
      this.frameId = null;
    }
    this.alternativeControl?.stop();
    this.alternativeControl = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    if (this.video) this.video.srcObject = null;
    this.detector = null;
  }

  private updateState(changes: Partial<EstadoEscanerCamara>): void {
    this.state = { ...this.state, ...changes };
    this.callbacks.onStateChange(this.getState());
  }
}
