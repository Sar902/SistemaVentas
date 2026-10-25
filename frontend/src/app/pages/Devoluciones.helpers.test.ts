import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import {
  agruparInventarioDevolucion,
  construirPayloadDevolucion,
  construirResolucionDevolucion,
  estadoDeSolicitud,
} from "./Devoluciones.helpers.ts";

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

test("separa por detalle dos costos del mismo producto y entrada", () => {
  const grupos = agruparInventarioDevolucion(
    [
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 102, detalleEntradaId: 12, estado: "Disponible" },
      { id: 103, detalleEntradaId: 11, estado: "Vendido" },
    ],
    detalles,
    productos,
  );

  assert.deepEqual(
    grupos.map((grupo) => ({
      key: grupo.key,
      detalleId: grupo.detalleId,
      entradaId: grupo.entradaId,
      costo: grupo.precioCompraUnitario,
      unidades: grupo.items.map((item) => item.id),
    })),
    [
      {
        key: "detalle-11",
        detalleId: 11,
        entradaId: 7,
        costo: "10.25",
        unidades: [101],
      },
      {
        key: "detalle-12",
        detalleId: 12,
        entradaId: 7,
        costo: "3.40",
        unidades: [102],
      },
    ],
  );
});

test("crea el payload minimo y elimina IDs de inventario repetidos", () => {
  const [grupo] = agruparInventarioDevolucion(
    [
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 101, detalleEntradaId: 11, estado: "Disponible" },
      { id: 104, detalleEntradaId: 11, estado: "Disponible" },
    ],
    detalles,
    productos,
  );

  assert.deepEqual(
    construirPayloadDevolucion(grupo, 2, "Empaque deteriorado", "Nota"),
    {
      IdEntradaInventario: 7,
      Observaciones: "Nota",
      detalles: [
        { inventarioId: 101, MotivoRechazo: "Empaque deteriorado" },
        { inventarioId: 104, MotivoRechazo: "Empaque deteriorado" },
      ],
    },
  );
});

test("aceptar genera una sola peticion POST a resolver", () => {
  assert.deepEqual(construirResolucionDevolucion(31, "Aceptada"), {
    method: "post",
    url: "/inventario/solicitudes-devolucion/31/resolver/",
    payload: { estado: "Aceptada" },
  });
});

test("rechazar genera una sola peticion POST a resolver", () => {
  assert.deepEqual(construirResolucionDevolucion(31, "Rechazada"), {
    method: "post",
    url: "/inventario/solicitudes-devolucion/31/resolver/",
    payload: { estado: "Rechazada" },
  });
});

test("conserva estados historicos y solo Pendiente es resoluble", () => {
  for (const estado of [
    "Pendiente",
    "Aceptada",
    "Rechazada",
    "ParcialmenteAceptada",
    "Aprobada",
  ]) {
    const normalizado = estadoDeSolicitud({ Estado: estado });
    assert.equal(normalizado.valor, estado);
    assert.equal(normalizado.puedeResolver, estado === "Pendiente");
  }
});

test("la pantalla no resuelve con PATCH ni con devolver-stock", () => {
  const source = readFileSync(
    new URL("./Devoluciones.tsx", import.meta.url),
    "utf8",
  );

  assert.doesNotMatch(source, /api\.patch\s*\(/);
  assert.doesNotMatch(source, /devolver-stock/);
  assert.match(source, /api\.post\(resolucion\.url, resolucion\.payload\)/);
});
