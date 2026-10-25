import assert from "node:assert/strict";
import test from "node:test";

import {
  MOTIVOS_PERDIDA,
  agruparInventarioDisponible,
  calcularTotalEstimado,
  construirPayloadPerdida,
  formatearMonto,
} from "./Perdidas.helpers.ts";

const productos = [
  { id: 5, name: "Producto repetido", presentacion: "Unidad" },
];
const detalles = [
  {
    id: 11,
    entradaInventarioId: 7,
    productoId: 5,
    precioCompraUnitario: "10.25",
  },
  {
    id: 12,
    entradaInventarioId: 7,
    productoId: 5,
    precioCompraUnitario: "3.40",
  },
];

test("separa detalles de la misma compra y producto usando el ID del detalle", () => {
  const grupos = agruparInventarioDisponible(
    [
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 102, detalleEntradaId: 12, estado: "Disponible" },
    ],
    detalles,
    productos,
  );

  assert.equal(grupos.length, 2);
  assert.deepEqual(
    grupos.map((grupo) => ({
      key: grupo.key,
      detalleId: grupo.detalleId,
      entradaId: grupo.entradaId,
      prodId: grupo.prodId,
      precioCompra: grupo.precioCompra,
      unidades: grupo.items.map((item) => item.id),
    })),
    [
      {
        key: "detalle-11",
        detalleId: 11,
        entradaId: 7,
        prodId: 5,
        precioCompra: 10.25,
        unidades: [101],
      },
      {
        key: "detalle-12",
        detalleId: 12,
        entradaId: 7,
        prodId: 5,
        precioCompra: 3.4,
        unidades: [102],
      },
    ],
  );
});

test("solo agrupa unidades disponibles y elimina IDs repetidos", () => {
  const grupos = agruparInventarioDisponible(
    [
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 102, detalleEntradaId: 11, estado: "Vendido" },
      { id: 103, detalleEntradaId: 11, estado: "Disponible" },
    ],
    detalles,
    productos,
  );

  assert.deepEqual(grupos[0].items.map((item) => item.id), [101, 103]);
});

test("calcula y formatea la estimación monetaria con dos decimales", () => {
  const [grupo] = agruparInventarioDisponible(
    [{ id: 101, detalleEntradaId: 11, estado: "Disponible" }],
    detalles,
    productos,
  );

  assert.equal(calcularTotalEstimado(grupo, 3), 30.75);
  assert.equal(formatearMonto(calcularTotalEstimado(grupo, 3)), "30.75");
  assert.equal(formatearMonto(3.4), "3.40");
});

test("construye el payload mínimo sin repetir inventario", () => {
  const [grupo] = agruparInventarioDisponible(
    [
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 103, detalleEntradaId: 11, estado: "Disponible" },
    ],
    detalles,
    productos,
  );

  assert.deepEqual(construirPayloadPerdida("Robo", grupo, 2), {
    tipoPerdida: "Robo",
    detalles: [{ inventarioId: 101 }, { inventarioId: 103 }],
  });
});

test("expone únicamente los cinco motivos manuales autorizados", () => {
  assert.deepEqual(
    MOTIVOS_PERDIDA.map((motivo) => motivo.value),
    ["Vencimiento", "Dañado", "Robo", "Uso Interno", "Otra"],
  );
});
