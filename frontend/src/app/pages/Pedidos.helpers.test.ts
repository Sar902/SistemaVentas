import assert from "node:assert/strict";
import test from "node:test";

import {
  construirDetallesPayload,
  productoPedidoDesdeDetalle,
  reemplazarLineaPedido,
  type ProductoPedido,
} from "./Pedidos.helpers.ts";

const detalleApi = (overrides: Record<string, unknown> = {}) => ({
  id: 3,
  productoId: 1,
  productoNombre: "Producto A",
  productoPresentacion: "Unidad",
  tipoCompra: "unidad",
  cantidad: 2,
  precioCompraUnitario: "10.00",
  ...overrides,
});

test("conserva el ID al cargar y enviar una edición sin cambios", () => {
  const linea = productoPedidoDesdeDetalle(detalleApi());

  assert.equal(linea.detalleId, 3);
  assert.deepEqual(construirDetallesPayload([linea]), [
    {
      id: 3,
      productoId: 1,
      tipoCompra: "unidad",
      cantidad: 2,
      precioCompraUnitario: "10.00",
    },
  ]);
});

test("conserva el ID al cambiar únicamente la cantidad", () => {
  const original = productoPedidoDesdeDetalle(detalleApi());
  const [editada] = reemplazarLineaPedido([original], original.clientKey, {
    productoId: original.productoId,
    nombre: original.nombre,
    presentacion: original.presentacion,
    tipoCompra: original.tipoCompra,
    cantidad: 3,
    cantidadMostrada: 3,
    unidadesPorPack: 1,
    precioUnitario: original.precioUnitario,
    totalPagado: 30,
  });

  assert.deepEqual(construirDetallesPayload([editada]), [
    {
      id: 3,
      productoId: 1,
      tipoCompra: "unidad",
      cantidad: 3,
      precioCompraUnitario: "10.00",
    },
  ]);
});

test("conserva el ID al reemplazar costo y tipo de una línea", () => {
  const original = productoPedidoDesdeDetalle(detalleApi());
  const [editada] = reemplazarLineaPedido([original], original.clientKey, {
    productoId: original.productoId,
    nombre: original.nombre,
    presentacion: original.presentacion,
    tipoCompra: "pack(3)",
    cantidad: 9,
    cantidadMostrada: 3,
    unidadesPorPack: 3,
    precioUnitario: 12.5,
    totalPagado: 112.5,
  });

  assert.deepEqual(construirDetallesPayload([editada]), [
    {
      id: 3,
      productoId: 1,
      tipoCompra: "pack(3)",
      cantidad: 9,
      precioCompraUnitario: "12.50",
    },
  ]);
});

test("un pack cargado conserva ID y separa packs de unidades físicas", () => {
  const linea = productoPedidoDesdeDetalle(
    detalleApi({ tipoCompra: "pack(3)", cantidad: 6 }),
  );

  assert.equal(linea.detalleId, 3);
  assert.equal(linea.cantidadMostrada, 2);
  assert.equal(linea.cantidad, 6);
  assert.equal(construirDetallesPayload([linea])[0].id, 3);
});

test("dos líneas del mismo producto conservan identidades distintas", () => {
  const primera = productoPedidoDesdeDetalle(detalleApi({ id: 3 }));
  const segunda = productoPedidoDesdeDetalle(detalleApi({ id: 7 }));
  const [sinCambios, editada] = reemplazarLineaPedido(
    [primera, segunda],
    segunda.clientKey,
    { ...segunda, cantidad: 4, cantidadMostrada: 4, totalPagado: 40 },
  );

  assert.equal(sinCambios.detalleId, 3);
  assert.equal(editada.detalleId, 7);
  assert.deepEqual(
    construirDetallesPayload([sinCambios, editada]).map((detalle) => detalle.id),
    [3, 7],
  );
});

test("una línea nueva omite el ID en el payload", () => {
  const nueva: ProductoPedido = {
    clientKey: "new-1",
    productoId: 1,
    nombre: "Producto A",
    tipoCompra: "unidad",
    cantidad: 3,
    cantidadMostrada: 3,
    unidadesPorPack: 1,
    precioUnitario: 10,
    totalPagado: 30,
  };

  assert.deepEqual(construirDetallesPayload([nueva]), [
    {
      productoId: 1,
      tipoCompra: "unidad",
      cantidad: 3,
      precioCompraUnitario: "10.00",
    },
  ]);
});
