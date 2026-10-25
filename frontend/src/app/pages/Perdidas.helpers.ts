export interface InventarioPerdidaApi {
  id: number | string;
  detalleEntradaId: number | string;
  estado: string;
}

export interface DetalleEntradaPerdidaApi {
  id: number | string;
  entradaInventarioId: number | string;
  productoId: number | string;
  precioCompraUnitario: number | string;
}

export interface ProductoPerdidaApi {
  id: number | string;
  name: string;
  presentacion?: string | null;
  Presentacion?: string | null;
}

export interface GrupoPerdida {
  key: string;
  detalleId: number | string;
  prodId: number | string;
  entradaId: number | string;
  nombre: string;
  presentacion?: string | null;
  precioCompra: number;
  items: InventarioPerdidaApi[];
}

export interface ProcesarPerdidaPayload {
  tipoPerdida: string;
  detalles: Array<{ inventarioId: number | string }>;
}

export const MOTIVOS_PERDIDA = [
  { value: "Vencimiento", label: "Vencimiento" },
  { value: "Dañado", label: "Dañado / Roto" },
  { value: "Robo", label: "Robo o Extravío" },
  { value: "Uso Interno", label: "Uso Interno" },
  { value: "Otra", label: "Otra" },
] as const;

export const formatearMonto = (valor: number | string): string => {
  const monto = Number(valor);
  return Number.isFinite(monto) ? monto.toFixed(2) : "0.00";
};

export const agruparInventarioDisponible = (
  inventario: InventarioPerdidaApi[],
  detalles: DetalleEntradaPerdidaApi[],
  productos: ProductoPerdidaApi[],
): GrupoPerdida[] => {
  const detallesPorId = new Map(
    detalles.map((detalle) => [String(detalle.id), detalle]),
  );
  const productosPorId = new Map(
    productos.map((producto) => [String(producto.id), producto]),
  );
  const grupos = new Map<string, GrupoPerdida>();
  const inventarioVisto = new Set<string>();

  for (const unidad of inventario) {
    const inventarioId = String(unidad.id);
    if (unidad.estado !== "Disponible" || inventarioVisto.has(inventarioId)) {
      continue;
    }
    inventarioVisto.add(inventarioId);

    const detalle = detallesPorId.get(String(unidad.detalleEntradaId));
    if (!detalle) continue;
    const producto = productosPorId.get(String(detalle.productoId));
    if (!producto) continue;

    const precioCompra = Number(detalle.precioCompraUnitario);
    if (!Number.isFinite(precioCompra)) continue;

    const key = `detalle-${detalle.id}`;
    const existente = grupos.get(key);
    if (existente) {
      existente.items.push(unidad);
      continue;
    }
    grupos.set(key, {
      key,
      detalleId: detalle.id,
      prodId: detalle.productoId,
      entradaId: detalle.entradaInventarioId,
      nombre: producto.name,
      presentacion: producto.presentacion || producto.Presentacion,
      precioCompra,
      items: [unidad],
    });
  }

  return [...grupos.values()];
};

export const calcularTotalEstimado = (
  grupo: GrupoPerdida,
  cantidad: number,
): number => Number((grupo.precioCompra * cantidad).toFixed(2));

export const construirPayloadPerdida = (
  tipoPerdida: string,
  grupo: GrupoPerdida,
  cantidad: number,
): ProcesarPerdidaPayload => {
  const idsUnicos = [...new Map(
    grupo.items.map((unidad) => [String(unidad.id), unidad.id]),
  ).values()];

  return {
    tipoPerdida,
    detalles: idsUnicos
      .slice(0, cantidad)
      .map((inventarioId) => ({ inventarioId })),
  };
};
