import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const readPage = (name: string) =>
  readFileSync(
    new URL("../pages/" + name + ".tsx", import.meta.url),
    "utf8",
  );

test("Productos conecta la cámara con crear y editar sin guardar automáticamente", () => {
  const source = readPage("Productos");

  assert.equal(
    source.match(/<CameraBarcodeScanner/g)?.length,
    2,
    "crear y editar deben ofrecer el escáner",
  );
  assert.match(source, /onDetected=\{setNewProductCode\}/);
  assert.match(source, /onDetected=\{\(code\) =>\s+setEditingProduct/);
  assert.doesNotMatch(
    source,
    /onDetected=\{handle(?:Add|Edit)Product\}/,
    "detectar no debe guardar el formulario",
  );
});

test("Ventas conecta la cámara con la cola de búsqueda y no con el cobro", () => {
  const source = readPage("Ventas");

  assert.match(source, /onDetected=\{enqueueBarcode\}/);
  assert.match(
    source,
    /const enqueueBarcode[\s\S]*encolarLecturaCodigo\([\s\S]*processBarcode/,
  );
  assert.doesNotMatch(source, /onDetected=\{handleCheckout\}/);
  assert.match(source, /status === 409[\s\S]*setUnknownBarcode\(null\)/);
});

test("Compras conecta la cámara con selección de producto y conserva cantidad y costo explícitos", () => {
  const source = readPage("Pedidos");

  assert.match(source, /onDetected=\{encolarCodigoCompra\}/);
  assert.match(source, /const encolarCodigoCompra[\s\S]*encolarLecturaCodigo/);
  const handler = source.match(
    /const procesarCodigoCompra[\s\S]*?\n  };\n\n  const buscarProductoPorCodigo/,
  )?.[0];
  assert.ok(handler, "debe existir el flujo de búsqueda por código");
  assert.match(handler, /setProductoId/);
  assert.doesNotMatch(handler, /setCantidad|setCostoCompra|agregarProducto/);
  assert.match(handler, /status === 409[\s\S]*setUnknownBarcode\(null\)/);
});
