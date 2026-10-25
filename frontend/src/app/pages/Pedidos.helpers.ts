export interface DetalleEntradaApi {
  id: number | string;
  productoId: number | string;
  productoNombre: string;
  productoPresentacion?: string | null;
  tipoCompra?: string | null;
  cantidad: number | string;
  precioCompraUnitario: number | string;
}

export interface ProductoPedido {
  detalleId?: number;
  clientKey: string;
  productoId: number;
  nombre: string;
  presentacion?: string | null;
  tipoCompra: string;
  cantidad: number;
  cantidadMostrada: number;
  unidadesPorPack: number;
  precioUnitario: number;
  totalPagado: number;
}

export interface DetalleCompraPayload {
  id?: number;
  productoId: number;
  tipoCompra: string;
  cantidad: number;
  precioCompraUnitario: string;
}

export const unidadesDePack = (tipoCompra: string): number | null => {
  const match = /^pack\((\d+)\)$/i.exec(tipoCompra);
  if (!match) return null;
  const unidades = Number(match[1]);
  return Number.isInteger(unidades) && unidades > 0 ? unidades : null;
};

export const productoPedidoDesdeDetalle = (
  detalle: DetalleEntradaApi,
): ProductoPedido => {
  const detalleId = Number(detalle.id);
  const tipoCompra = detalle.tipoCompra ?? "unidad";
  const unidadesPorPack = unidadesDePack(tipoCompra) ?? 1;
  const cantidad = Number(detalle.cantidad);
  const precioUnitario = Number(detalle.precioCompraUnitario);

  return {
    detalleId,
    clientKey: `existing-${detalleId}`,
    productoId: Number(detalle.productoId),
    nombre: detalle.productoNombre,
    presentacion: detalle.productoPresentacion,
    tipoCompra,
    cantidad,
    cantidadMostrada:
      unidadesDePack(tipoCompra) === null
        ? cantidad
        : cantidad / unidadesPorPack,
    unidadesPorPack,
    precioUnitario,
    totalPagado: cantidad * precioUnitario,
  };
};

export const reemplazarLineaPedido = (
  lineas: ProductoPedido[],
  clientKey: string,
  cambios: Omit<ProductoPedido, "detalleId" | "clientKey">,
): ProductoPedido[] =>
  lineas.map((linea) =>
    linea.clientKey === clientKey
      ? {
          ...cambios,
          detalleId: linea.detalleId,
          clientKey: linea.clientKey,
        }
      : linea,
  );

export const construirDetallesPayload = (
  lineas: ProductoPedido[],
): DetalleCompraPayload[] =>
  lineas.map((linea) => ({
    ...(linea.detalleId !== undefined ? { id: linea.detalleId } : {}),
    productoId: linea.productoId,
    tipoCompra: linea.tipoCompra,
    cantidad: linea.cantidad,
    precioCompraUnitario: linea.precioUnitario.toFixed(2),
  }));
