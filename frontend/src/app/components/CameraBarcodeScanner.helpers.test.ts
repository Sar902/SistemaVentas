import assert from "node:assert/strict";
import test from "node:test";

import {
  FORMATOS_CODIGO_OBJETIVO,
  SesionEscanerCamara,
  type DetectorCodigo,
  type DetectorAlternativo,
  type EntornoEscanerCamara,
  type EstadoEscanerCamara,
  type FlujoCamara,
  type ResultadoDeteccion,
  type VideoCamara,
} from "./CameraBarcodeScanner.helpers.ts";

const flushPromises = () =>
  new Promise<void>((resolve) => {
    setImmediate(resolve);
  });

const deferred = <T>() => {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
};

const createStream = (deviceId = "rear") => {
  let stops = 0;
  const track = {
    stop: () => {
      stops += 1;
    },
    getSettings: () => ({ deviceId }),
  };
  const stream: FlujoCamara = {
    getTracks: () => [track],
    getVideoTracks: () => [track],
  };
  return { stream, getStops: () => stops };
};

const createHarness = ({
  formats = [...FORMATOS_CODIGO_OBJETIVO],
  detect = async () => [],
  getUserMedia,
}: {
  formats?: string[];
  detect?: DetectorCodigo["detect"];
  getUserMedia?: EntornoEscanerCamara["getUserMedia"];
} = {}) => {
  const frames = new Map<number, () => void>();
  let nextFrame = 1;
  const requestedFormats: string[][] = [];
  const requestedConstraints: unknown[] = [];
  const states: EstadoEscanerCamara[] = [];
  const detected: Array<{ code: string; format?: string }> = [];
  const defaultStream = createStream();
  const video: VideoCamara = {
    srcObject: null,
    play: async () => undefined,
  };
  let mediaRequests = 0;

  const environment: EntornoEscanerCamara = {
    secureContext: true,
    getSupportedFormats: async () => formats,
    createDetector: (selectedFormats) => {
      requestedFormats.push([...selectedFormats]);
      return { detect };
    },
    getUserMedia: async (constraints) => {
      mediaRequests += 1;
      requestedConstraints.push(constraints);
      if (getUserMedia) return getUserMedia(constraints);
      return defaultStream.stream;
    },
    enumerateCameras: async () => [
      { deviceId: "rear", label: "Trasera" },
      { deviceId: "front", label: "Frontal" },
    ],
    requestFrame: (callback) => {
      const id = nextFrame++;
      frames.set(id, callback);
      return id;
    },
    cancelFrame: (id) => {
      frames.delete(id);
    },
  };
  const session = new SesionEscanerCamara(environment, {
    onStateChange: (state) => states.push(state),
    onDetected: (code, format) => detected.push({ code, format }),
  });

  return {
    session,
    environment,
    video,
    states,
    detected,
    requestedFormats,
    requestedConstraints,
    defaultStream,
    getMediaRequests: () => mediaRequests,
    runFrame: async () => {
      const entry = frames.entries().next().value as
        | [number, () => void]
        | undefined;
      assert.ok(entry, "se esperaba una detección programada");
      frames.delete(entry[0]);
      entry[1]();
      await flushPromises();
    },
    getPendingFrames: () => frames.size,
  };
};

test("solo solicita permiso al abrir, prefiere cámara trasera y libera tracks al cerrar", async () => {
  const harness = createHarness();
  assert.equal(harness.getMediaRequests(), 0);

  await harness.session.open(harness.video);

  assert.equal(harness.getMediaRequests(), 1);
  assert.deepEqual(harness.requestedConstraints[0], {
    audio: false,
    video: {
      facingMode: { ideal: "environment" },
      width: { ideal: 1280 },
      height: { ideal: 720 },
    },
  });
  assert.equal(harness.session.getState().phase, "scanning");
  assert.equal(harness.session.getState().cameras.length, 2);
  assert.equal(harness.video.srcObject, harness.defaultStream.stream);

  harness.session.close();

  assert.equal(harness.defaultStream.getStops(), 1);
  assert.equal(harness.video.srcObject, null);
  assert.equal(harness.getPendingFrames(), 0);
});

test("muestra una alternativa controlada cuando el permiso es denegado", async () => {
  const permissionError = Object.assign(new Error("denied"), {
    name: "NotAllowedError",
  });
  const harness = createHarness({
    getUserMedia: async () => {
      throw permissionError;
    },
  });

  await harness.session.open(harness.video);

  assert.equal(harness.session.getState().phase, "error");
  assert.match(harness.session.getState().error ?? "", /permisos del navegador/);
  assert.equal(harness.video.srcObject, null);
});

test("diferencia la ausencia de la API de cámara", async () => {
  const harness = createHarness();
  harness.environment.getUserMedia = undefined;

  await harness.session.open(harness.video);

  assert.equal(harness.session.getState().phase, "error");
  assert.match(harness.session.getState().error ?? "", /no ofrece acceso a una cámara/);
});

test("una detección simultánea se entrega una sola vez y pausa la cámara", async () => {
  const harness = createHarness({
    detect: async () => [
      { rawValue: "  0001234567890  ", format: "ean_13" },
      { rawValue: "OTRO", format: "code_128" },
    ],
  });
  await harness.session.open(harness.video);

  await harness.runFrame();

  assert.deepEqual(harness.detected, [
    { code: "0001234567890", format: "ean_13" },
  ]);
  assert.equal(harness.session.getState().phase, "detected");
  assert.equal(harness.defaultStream.getStops(), 1);
  assert.equal(harness.getPendingFrames(), 0);
});

test("escanear otro permite repetir deliberadamente el mismo código", async () => {
  const streams = [createStream("rear"), createStream("rear")];
  let streamIndex = 0;
  const harness = createHarness({
    detect: async () => [{ rawValue: "ABC-001", format: "code_128" }],
    getUserMedia: async () => streams[streamIndex++].stream,
  });

  await harness.session.open(harness.video);
  await harness.runFrame();
  await harness.session.open(harness.video, "rear");
  await harness.runFrame();

  assert.deepEqual(
    harness.detected.map(({ code }) => code),
    ["ABC-001", "ABC-001"],
  );
  assert.deepEqual(harness.requestedConstraints[1], {
    audio: false,
    video: { deviceId: { exact: "rear" } },
  });
  assert.equal(streams[0].getStops(), 1);
  assert.equal(streams[1].getStops(), 1);
});

test("ignora aperturas y detecciones tardías después de cerrar", async () => {
  const pendingStream = deferred<FlujoCamara>();
  const lateStream = createStream();
  const openingHarness = createHarness({
    getUserMedia: () => pendingStream.promise,
  });
  const opening = openingHarness.session.open(openingHarness.video);
  await flushPromises();
  openingHarness.session.close();
  pendingStream.resolve(lateStream.stream);
  await opening;

  assert.equal(lateStream.getStops(), 1);
  assert.equal(openingHarness.session.getState().phase, "idle");

  const pendingDetection = deferred<ResultadoDeteccion[]>();
  const detectionHarness = createHarness({
    detect: () => pendingDetection.promise,
  });
  await detectionHarness.session.open(detectionHarness.video);
  const frame = detectionHarness.runFrame();
  detectionHarness.session.close();
  pendingDetection.resolve([{ rawValue: "7501234567890", format: "ean_13" }]);
  await frame;

  assert.deepEqual(detectionHarness.detected, []);
  assert.equal(detectionHarness.session.getState().phase, "idle");
});

test("cambiar de cámara detiene la anterior y usa el dispositivo elegido", async () => {
  const streams = [createStream("rear"), createStream("front")];
  let streamIndex = 0;
  const harness = createHarness({
    getUserMedia: async () => streams[streamIndex++].stream,
  });
  await harness.session.open(harness.video);

  await harness.session.open(harness.video, "front");

  assert.equal(streams[0].getStops(), 1);
  assert.equal(harness.session.getState().selectedDeviceId, "front");
  assert.deepEqual(harness.requestedConstraints[1], {
    audio: false,
    video: { deviceId: { exact: "front" } },
  });
  harness.session.close();
  assert.equal(streams[1].getStops(), 1);
});

test("comprueba en ejecución EAN-13, EAN-8, UPC-A y Code 128", async () => {
  const samples = [
    { rawValue: "7501234567890", format: "ean_13" },
    { rawValue: "12345670", format: "ean_8" },
    { rawValue: "012345678905", format: "upc_a" },
    { rawValue: "LOTE-ABC-001", format: "code_128" },
  ];

  for (const sample of samples) {
    const harness = createHarness({
      detect: async () => [sample],
    });
    await harness.session.open(harness.video);
    await harness.runFrame();

    assert.deepEqual(harness.requestedFormats[0], [
      "ean_13",
      "ean_8",
      "upc_a",
      "code_128",
    ]);
    assert.deepEqual(harness.detected, [
      { code: sample.rawValue, format: sample.format },
    ]);
  }
});

test("rechaza contexto inseguro o ausencia total de motores sin abrir la cámara", async () => {
  const insecure = createHarness();
  insecure.environment.secureContext = false;
  await insecure.session.open(insecure.video);
  assert.equal(insecure.getMediaRequests(), 0);
  assert.match(insecure.session.getState().error ?? "", /HTTPS/);

  const unsupported = createHarness({ formats: ["qr_code"] });
  unsupported.environment.loadAlternativeDetector = undefined;
  await unsupported.session.open(unsupported.video);
  assert.equal(unsupported.getMediaRequests(), 0);
  assert.match(unsupported.session.getState().error ?? "", /lector alternativo/);

  const noDetector = createHarness();
  noDetector.environment.getSupportedFormats = undefined;
  noDetector.environment.createDetector = undefined;
  noDetector.environment.loadAlternativeDetector = undefined;
  await noDetector.session.open(noDetector.video);
  assert.equal(noDetector.getMediaRequests(), 0);
  assert.match(noDetector.session.getState().error ?? "", /lector alternativo/);
});

const configureAlternative = (
  harness: ReturnType<typeof createHarness>,
  detector?: DetectorAlternativo,
) => {
  let loads = 0;
  let starts = 0;
  let stops = 0;
  let emit: ((result: ResultadoDeteccion) => void) | null = null;
  harness.environment.loadAlternativeDetector = async () => {
    loads += 1;
    return detector ?? {
      start: async (_stream, _video, onDetected) => {
        starts += 1;
        emit = onDetected;
        return {
          stop: () => {
            stops += 1;
          },
        };
      },
    };
  };
  return {
    getLoads: () => loads,
    getStarts: () => starts,
    getStops: () => stops,
    emit: (result: ResultadoDeteccion) => {
      assert.ok(emit, "se esperaba que ZXing estuviera activo");
      emit(result);
    },
  };
};

test("sin BarcodeDetector activa ZXing de forma diferida", async () => {
  const harness = createHarness();
  harness.environment.getSupportedFormats = undefined;
  harness.environment.createDetector = undefined;
  const alternative = configureAlternative(harness);

  assert.equal(alternative.getLoads(), 0);
  await harness.session.open(harness.video);

  assert.equal(alternative.getLoads(), 1);
  assert.equal(alternative.getStarts(), 1);
  assert.equal(harness.session.getState().engine, "zxing");
  assert.equal(harness.getPendingFrames(), 0);

  alternative.emit({ rawValue: "  0012345678905  ", format: "upc_a" });
  alternative.emit({ rawValue: "OTRO", format: "code_128" });

  assert.deepEqual(harness.detected, [
    { code: "0012345678905", format: "upc_a" },
  ]);
  assert.equal(alternative.getStops(), 1);
  assert.equal(harness.defaultStream.getStops(), 1);
});

test("usa ZXing si el detector nativo no cubre todos los formatos", async () => {
  const harness = createHarness({ formats: ["ean_13", "ean_8"] });
  const alternative = configureAlternative(harness);

  await harness.session.open(harness.video);

  assert.equal(harness.session.getState().engine, "zxing");
  assert.equal(alternative.getLoads(), 1);
  assert.deepEqual(harness.requestedFormats, []);
  harness.session.close();
});

test("no carga ZXing cuando el detector nativo cubre los cuatro formatos", async () => {
  const harness = createHarness();
  const alternative = configureAlternative(harness);

  await harness.session.open(harness.video);

  assert.equal(harness.session.getState().engine, "native");
  assert.equal(alternative.getLoads(), 0);
  assert.deepEqual(harness.requestedFormats, [
    ["ean_13", "ean_8", "upc_a", "code_128"],
  ]);
  harness.session.close();
});

test("ZXing permite repetir solo al iniciar otra sesión", async () => {
  const harness = createHarness({ formats: [] });
  const alternative = configureAlternative(harness);

  await harness.session.open(harness.video);
  alternative.emit({ rawValue: "LOTE-001", format: "code_128" });
  await harness.session.open(harness.video);
  alternative.emit({ rawValue: "LOTE-001", format: "code_128" });

  assert.deepEqual(
    harness.detected.map(({ code }) => code),
    ["LOTE-001", "LOTE-001"],
  );
  assert.equal(alternative.getLoads(), 2);
  assert.equal(alternative.getStops(), 2);
});

test("cerrar durante la carga alternativa evita abrir la cámara", async () => {
  const harness = createHarness({ formats: [] });
  const pendingDetector = deferred<DetectorAlternativo>();
  harness.environment.loadAlternativeDetector = () => pendingDetector.promise;

  const opening = harness.session.open(harness.video);
  await flushPromises();
  harness.session.close();
  pendingDetector.resolve({
    start: async () => ({ stop: () => undefined }),
  });
  await opening;

  assert.equal(harness.getMediaRequests(), 0);
  assert.equal(harness.session.getState().phase, "idle");
});

test("detención tardía de ZXing libera su control y no duplica callbacks", async () => {
  const harness = createHarness({ formats: [] });
  const pendingControl = deferred<{ stop(): void }>();
  let emit: ((result: ResultadoDeteccion) => void) | null = null;
  let stops = 0;
  configureAlternative(harness, {
    start: async (_stream, _video, onDetected) => {
      emit = onDetected;
      return pendingControl.promise;
    },
  });

  const opening = harness.session.open(harness.video);
  await flushPromises();
  assert.ok(emit);
  emit({ rawValue: "00000070", format: "ean_8" });
  emit({ rawValue: "00000071", format: "ean_8" });
  pendingControl.resolve({
    stop: () => {
      stops += 1;
    },
  });
  await opening;

  assert.deepEqual(harness.detected, [
    { code: "00000070", format: "ean_8" },
  ]);
  assert.equal(stops, 1);
  assert.equal(harness.defaultStream.getStops(), 1);
});
