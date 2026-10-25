export interface ProductoEscaneable {
  id: number;
  availableQty: number;
  inventarioIds: number[];
  name?: string;
  nombre?: string;
  salePrice?: number;
  imagen?: string | null;
}

export interface LineaCarritoEscaneada {
  productoId: number;
  quantity: number;
  inventarioIds: number[];
  [key: string]: unknown;
}

export type ResultadoAgregarUnidad =
  | "agregado"
  | "incrementado"
  | "sin_stock"
  | "producto_invalido";

export const normalizarEntradaCodigo = (value: string): string | null => {
  const normalized = value.trim();
  return normalized || null;
};

export const mensajeErrorBusquedaCodigo = (
  error: unknown,
  fallback = "No se pudo buscar el código.",
): string => {
  const responseData = (error as {
    response?: { data?: { code?: string[]; detail?: string } };
  })?.response?.data;
  return responseData?.code?.[0] || responseData?.detail || fallback;
};

export const agregarUnidadEscaneada = (
  cart: LineaCarritoEscaneada[],
  product: ProductoEscaneable | null | undefined,
): { cart: LineaCarritoEscaneada[]; result: ResultadoAgregarUnidad } => {
  if (!product) return { cart, result: "producto_invalido" };

  const stockUtilizable = Math.min(
    product.availableQty,
    product.inventarioIds.length,
  );
  const existing = cart.find((line) => line.productoId === product.id);
  if (existing) {
    if (existing.quantity >= stockUtilizable) {
      return { cart, result: "sin_stock" };
    }
    return {
      cart: cart.map((line) =>
        line.productoId === product.id
          ? { ...line, quantity: line.quantity + 1 }
          : line,
      ),
      result: "incrementado",
    };
  }
  if (stockUtilizable <= 0) return { cart, result: "sin_stock" };

  return {
    cart: [
      ...cart,
      {
        productoId: product.id,
        name: product.name ?? product.nombre,
        price: Number(product.salePrice ?? 0),
        quantity: 1,
        inventarioIds: [...product.inventarioIds],
        imagen: product.imagen ?? null,
      },
    ],
    result: "agregado",
  };
};

export const construirDetallesVenta = (cart: LineaCarritoEscaneada[]) => {
  const used = new Set<number>();
  return cart.flatMap((line) => {
    const selected = line.inventarioIds.slice(0, line.quantity);
    if (selected.length !== line.quantity) {
      throw new Error("No hay suficientes unidades identificadas para la venta.");
    }
    return selected.map((inventarioId) => {
      if (used.has(inventarioId)) {
        throw new Error("Una unidad de inventario está repetida en el carrito.");
      }
      used.add(inventarioId);
      return { inventarioId };
    });
  });
};

export const encolarLecturaCodigo = <T>(
  queue: Promise<unknown>,
  task: () => Promise<T>,
  onError: (error: unknown) => void,
): Promise<void> =>
  queue
    .catch(() => undefined)
    .then(task)
    .then(() => undefined)
    .catch((error) => {
      onError(error);
    });
