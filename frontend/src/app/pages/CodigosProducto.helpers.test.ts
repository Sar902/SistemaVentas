import assert from "node:assert/strict";
import test from "node:test";

import {
  agregarUnidadEscaneada,
  construirDetallesVenta,
  encolarLecturaCodigo,
  mensajeErrorBusquedaCodigo,
  normalizarEntradaCodigo,
} from "./CodigosProducto.helpers.ts";

const product = {
  id: 7,
  name: "Producto",
  availableQty: 2,
  inventarioIds: [41, 42],
  salePrice: 15,
};

test("Enter puede normalizar una entrada manual sin perder ceros", () => {
  assert.equal(normalizarEntradaCodigo("  000123ABC  "), "000123ABC");
  assert.equal(normalizarEntradaCodigo("   "), null);
});

test("una respuesta por equivalencia agrega como máximo una unidad", () => {
  const equivalentResponse = {
    ...product,
    code: "0012345678905",
  };

  const result = agregarUnidadEscaneada([], equivalentResponse);

  assert.equal(result.result, "agregado");
  assert.equal(result.cart.length, 1);
  assert.equal(result.cart[0].quantity, 1);
});

test("una ambigüedad 409 muestra el detalle y no modifica el carrito", async () => {
  const originalCart = agregarUnidadEscaneada([], product).cart;
  let message = "";

  await encolarLecturaCodigo(
    Promise.resolve(),
    async () => {
      throw {
        response: {
          status: 409,
          data: { detail: "El código coincide con más de un producto." },
        },
      };
    },
    (error) => {
      message = mensajeErrorBusquedaCodigo(error);
    },
  );

  assert.equal(message, "El código coincide con más de un producto.");
  assert.equal(originalCart.length, 1);
  assert.equal(originalCart[0].quantity, 1);
});

test("lecturas repetidas incrementan una sola línea hasta el stock", () => {
  const first = agregarUnidadEscaneada([], product);
  const second = agregarUnidadEscaneada(first.cart, product);
  const exhausted = agregarUnidadEscaneada(second.cart, product);

  assert.equal(first.result, "agregado");
  assert.equal(second.result, "incrementado");
  assert.equal(second.cart.length, 1);
  assert.equal(second.cart[0].quantity, 2);
  assert.equal(exhausted.result, "sin_stock");
  assert.deepEqual(exhausted.cart, second.cart);
});

test("la venta selecciona unidades distintas y rechaza una repetida", () => {
  const twoUnits = agregarUnidadEscaneada(
    agregarUnidadEscaneada([], product).cart,
    product,
  ).cart;
  assert.deepEqual(construirDetallesVenta(twoUnits), [
    { inventarioId: 41 },
    { inventarioId: 42 },
  ]);

  assert.throws(
    () =>
      construirDetallesVenta([
        twoUnits[0],
        { productoId: 8, quantity: 1, inventarioIds: [42] },
      ]),
    /repetida/,
  );
});

test("búsquedas rápidas se procesan en orden y una desconocida no corta la cola", async () => {
  const started: string[] = [];
  const finished: string[] = [];
  const errors: string[] = [];
  let releaseFirst!: () => void;
  let confirmFirstStarted!: () => void;
  const firstGate = new Promise<void>((resolve) => {
    releaseFirst = resolve;
  });
  const firstStarted = new Promise<void>((resolve) => {
    confirmFirstStarted = resolve;
  });

  let queue: Promise<unknown> = Promise.resolve();
  queue = encolarLecturaCodigo(
    queue,
    async () => {
      started.push("primera");
      confirmFirstStarted();
      await firstGate;
      finished.push("primera");
    },
    () => undefined,
  );
  queue = encolarLecturaCodigo(
    queue,
    async () => {
      started.push("desconocida");
      throw new Error("Código desconocido");
    },
    (error) => errors.push((error as Error).message),
  );
  queue = encolarLecturaCodigo(
    queue,
    async () => {
      started.push("tercera");
      finished.push("tercera");
    },
    () => undefined,
  );

  await firstStarted;
  assert.deepEqual(started, ["primera"]);
  releaseFirst();
  await queue;

  assert.deepEqual(started, ["primera", "desconocida", "tercera"]);
  assert.deepEqual(finished, ["primera", "tercera"]);
  assert.deepEqual(errors, ["Código desconocido"]);
});
