import assert from "node:assert/strict";
import test from "node:test";

import {
  BarcodeFormat,
  BinaryBitmap,
  DecodeHintType,
  HybridBinarizer,
  MultiFormatReader,
  RGBLuminanceSource,
} from "@zxing/library";

const L = [
  "0001101",
  "0011001",
  "0010011",
  "0111101",
  "0100011",
  "0110001",
  "0101111",
  "0111011",
  "0110111",
  "0001011",
];
const G = [
  "0100111",
  "0110011",
  "0011011",
  "0100001",
  "0011101",
  "0111001",
  "0000101",
  "0010001",
  "0001001",
  "0010111",
];
const R = L.map((pattern) =>
  [...pattern].map((bit) => (bit === "1" ? "0" : "1")).join(""),
);
const PARIDAD_EAN13 = [
  "LLLLLL",
  "LLGLGG",
  "LLGGLG",
  "LLGGGL",
  "LGLLGG",
  "LGGLLG",
  "LGGGLL",
  "LGLGLG",
  "LGLGGL",
  "LGGLGL",
];

const bitsEAN13 = (value: string) => {
  const digits = [...value].map(Number);
  const parity = PARIDAD_EAN13[digits[0]];
  const left = digits
    .slice(1, 7)
    .map((digit, index) => (parity[index] === "L" ? L : G)[digit])
    .join("");
  const right = digits.slice(7).map((digit) => R[digit]).join("");
  return `101${left}01010${right}101`;
};

const bitsEAN8 = (value: string) => {
  const digits = [...value].map(Number);
  return `101${digits.slice(0, 4).map((digit) => L[digit]).join("")}01010${digits.slice(4).map((digit) => R[digit]).join("")}101`;
};

const bitsCode128 = () => {
  const widths = [
    "211214",
    "111323",
    "131123",
    "131321",
    "122132",
    "123122",
    "123122",
    "123221",
    "211313",
    "2331112",
  ];
  return widths
    .map((pattern) =>
      [...pattern]
        .map((width, index) => (index % 2 === 0 ? "1" : "0").repeat(Number(width)))
        .join(""),
    )
    .join("");
};

const decodificarImagen = (bits: string, format: BarcodeFormat) => {
  const scale = 4;
  const height = 120;
  const quiet = "0".repeat(12);
  const row = [...`${quiet}${bits}${quiet}`].flatMap((bit) =>
    Array<string>(scale).fill(bit),
  );
  const pixels = new Uint8ClampedArray(row.length * height);
  for (let y = 0; y < height; y += 1) {
    row.forEach((bit, x) => {
      pixels[y * row.length + x] = bit === "1" ? 0 : 255;
    });
  }
  const bitmap = new BinaryBitmap(
    new HybridBinarizer(
      new RGBLuminanceSource(pixels, row.length, height),
    ),
  );
  const hints = new Map<DecodeHintType, unknown>([
    [DecodeHintType.POSSIBLE_FORMATS, [format]],
    [DecodeHintType.TRY_HARDER, true],
  ]);
  return new MultiFormatReader().decode(bitmap, hints);
};

test("ZXing decodifica imágenes EAN-13, EAN-8, UPC-A y Code 128", () => {
  const samples = [
    {
      bits: bitsEAN13("4006381333931"),
      format: BarcodeFormat.EAN_13,
      expected: "4006381333931",
    },
    {
      bits: bitsEAN8("00000000"),
      format: BarcodeFormat.EAN_8,
      expected: "00000000",
    },
    {
      bits: bitsEAN13("0012345678905"),
      format: BarcodeFormat.UPC_A,
      expected: "012345678905",
    },
    {
      bits: bitsCode128(),
      format: BarcodeFormat.CODE_128,
      expected: "ABC-001",
    },
  ];

  for (const sample of samples) {
    const result = decodificarImagen(sample.bits, sample.format);
    assert.equal(result.getText(), sample.expected);
    assert.equal(result.getBarcodeFormat(), sample.format);
  }
});
