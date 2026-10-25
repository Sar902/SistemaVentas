import { BrowserMultiFormatReader } from "@zxing/browser";
import { BarcodeFormat } from "@zxing/library";

import type {
  DetectorAlternativo,
  FlujoCamara,
  ResultadoDeteccion,
  VideoCamara,
} from "./CameraBarcodeScanner.helpers";

const FORMATOS_ZXING = [
  BarcodeFormat.EAN_13,
  BarcodeFormat.EAN_8,
  BarcodeFormat.UPC_A,
  BarcodeFormat.CODE_128,
];

const nombresFormato: Partial<Record<BarcodeFormat, string>> = {
  [BarcodeFormat.EAN_13]: "ean_13",
  [BarcodeFormat.EAN_8]: "ean_8",
  [BarcodeFormat.UPC_A]: "upc_a",
  [BarcodeFormat.CODE_128]: "code_128",
};

export const crearDetectorZXing = (): DetectorAlternativo => ({
  async start(stream, video, onDetected) {
    const reader = new BrowserMultiFormatReader();
    reader.possibleFormats = FORMATOS_ZXING;

    const controls = await reader.decodeFromStream(
      stream as unknown as MediaStream,
      video as unknown as HTMLVideoElement,
      (result) => {
        if (!result) return;
        const detection: ResultadoDeteccion = {
          rawValue: result.getText(),
          format: nombresFormato[result.getBarcodeFormat()],
        };
        onDetected(detection);
      },
    );

    return { stop: () => controls.stop() };
  },
});
