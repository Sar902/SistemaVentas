export interface InventarioDevolucionApi {
  id: number | string;
  detalleEntradaId: number | string;
  estado: string;
}

export interface DetalleEntradaDevolucionApi {
  id: number | string;
  entradaInventarioId: number | string;
  productoId: number | string;
  precioCompraUnitario: number | string;
}

export interface ProductoDevolucionApi {
  id: number | string;
  name: string;
  presentacion?: string | null;
  Presentacion?: string | null;
}

export interface GrupoDevolucion {
  key: string;
  detalleId: number | string;
  prodId: number | string;
  entradaId: number | string;
  nombre: string;
  presentacion?: string | null;
  precioCompraUnitario: number | string;
  items: InventarioDevolucionApi[];
}

export interface ProcesarDevolucionPayload {
  IdEntradaInventario: number | string;
  Observaciones: string;
  detalles: Array<{
    inventarioId: number | string;
    MotivoRechazo?: string;
  }>;
}

export type EstadoResolucion = "Aceptada" | "Rechazada";

export const agruparInventarioDevolucion = (
  inventario: InventarioDevolucionApi[],
  detalles: DetalleEntradaDevolucionApi[],
  productos: ProductoDevolucionApi[],
): GrupoDevolucion[] => {
  const detallesPorId = new Map(
    detalles.map((detalle) => [String(detalle.id), detalle]),
  );
  const productosPorId = new Map(
    productos.map((producto) => [String(producto.id), producto]),
  );
  const grupos = new Map<string, GrupoDevolucion>();
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
      precioCompraUnitario: detalle.precioCompraUnitario,
      items: [unidad],
    });
  }

  return [...grupos.values()];
};

export const construirPayloadDevolucion = (
  grupo: GrupoDevolucion,
  cantidad: number,
  motivo: string,
  observaciones: string,
): ProcesarDevolucionPayload => {
  const idsUnicos = [...new Map(
    grupo.items.map((unidad) => [String(unidad.id), unidad.id]),
  ).values()];
  const motivoLimpio = motivo.trim();

  return {
    IdEntradaInventario: grupo.entradaId,
    Observaciones: observaciones,
    detalles: idsUnicos.slice(0, cantidad).map((inventarioId) => ({
      inventarioId,
      ...(motivoLimpio ? { MotivoRechazo: motivoLimpio } : {}),
    })),
  };
};

export const construirResolucionDevolucion = (
  solicitudId: number | string,
  estado: EstadoResolucion,
) => ({
  method: "post" as const,
  url: `/inventario/solicitudes-devolucion/${solicitudId}/resolver/`,
  payload: { estado },
});

export const estadoDeSolicitud = (solicitud: {
  estado?: unknown;
  Estado?: unknown;
}) => {
  const recibido = solicitud.estado ?? solicitud.Estado;
  const valor = typeof recibido === "string" && recibido ? recibido : "Pendiente";
  return { valor, puedeResolver: valor === "Pendiente" };
};
