import { useEffect, useState, useRef } from "react";
import api from "../api/axiosInstance";
import { Card } from "../components/ui/card";
import {
  construirDetallesPayload,
  productoPedidoDesdeDetalle,
  reemplazarLineaPedido,
  unidadesDePack,
  type DetalleEntradaApi,
  type ProductoPedido,
} from "./Pedidos.helpers";

import { Input } from "../components/ui/input";
import { Button } from "../components/ui/button";
import {
  Search,
  Store,
  Phone,
  Plus,
  Check,
  Trash2,
  Pencil,
  Eye,
  ArrowLeft,
} from "lucide-react";
import { toast } from "sonner";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "../components/ui/dialog";
import { Label } from "../components/ui/label";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";

import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from "../components/ui/table";

import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";

/* =========================
   TIPOS (con serializer nuevo)
========================= */

interface Proveedor {
  id: number;
  name: string;
  contact: string | null;
  status: string;

  // NUEVO serializer
  pedidos_recientes: number;
  activo: boolean;
}

const mensajeApi = (error: unknown, fallback: string) => {
  const data = (
    error as { response?: { data?: unknown } }
  )?.response?.data;
  if (typeof data === "string") {
    const text = data.trim();
    return text && text.length <= 240 ? text : fallback;
  }
  if (data && typeof data === "object") {
    const record = data as Record<string, unknown>;
    for (const key of ["error", "detail"]) {
      const value = record[key];
      if (typeof value === "string" && value.trim()) return value;
      if (Array.isArray(value) && typeof value[0] === "string") {
        return value[0];
      }
    }
  }
  return fallback;
};

/* =========================
   COMPONENTE
========================= */

export function Pedidos() {
  const [proveedorSeleccionado, setProveedorSeleccionado] =
    useState<Proveedor | null>(null);
  const [proveedores, setProveedores] = useState<Proveedor[]>([]);
  const [busqueda, setBusqueda] = useState("");

  const [modalProveedor, setModalProveedor] = useState(false);
  const [mostrarPedidoView, setMostrarPedidoView] = useState(false);
  const [successMessage, setSuccessMessage] = useState(false);
  // MEJ-010: previene fuga de memoria al desmontar el componente
  const successTimerRef = useRef<number | null>(null);
  const viewPedidoTriggerRef = useRef<HTMLButtonElement | null>(null);
  const deletePedidoTriggerRef = useRef<HTMLButtonElement | null>(null);
  const proveedorDialogTriggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (successMessage) {
      successTimerRef.current = window.setTimeout(
        () => setSuccessMessage(false),
        2500,
      );
    }
    return () => {
      if (successTimerRef.current !== null) {
        clearTimeout(successTimerRef.current);
      }
    };
  }, [successMessage]);

  const [nuevoProveedor, setNuevoProveedor] = useState({
    name: "",
    contact: "",
  });

  const [entradas, setEntradas] = useState<any[]>([]);
  const [inventario, setInventario] = useState<any[]>([]);
  const [productos, setProductos] = useState<any[]>([]);

  const [isAddMode, setIsAddMode] = useState(false);

  const [selectedProveedor, setSelectedProveedor] = useState("");
  // FORM PRODUCTO (CORRECTO)
  const [productoId, setProductoId] = useState("");
  const [cantidad, setCantidad] = useState("");
  const [costoCompra, setCostoCompra] = useState("");

  const [productosPedido, setProductosPedido] = useState<ProductoPedido[]>([]);
  const [editingLineKey, setEditingLineKey] = useState<string | null>(null);
  const [isSavingPedido, setIsSavingPedido] = useState(false);
  const [isDeletingPedido, setIsDeletingPedido] = useState(false);

  // Estados para pedidos
  const [viewingPedido, setViewingPedido] = useState<any>(null);
  const [isViewPedidoOpen, setIsViewPedidoOpen] = useState(false);

  const [editingPedido, setEditingPedido] = useState<any>(null);
  const [isEditPedidoOpen, setIsEditPedidoOpen] = useState(false);

  const [deletePedidoAlert, setDeletePedidoAlert] = useState({
    isOpen: false,
    pedidoId: null,
    puedeEliminar: false,
  });

  const [tipoCompra, setTipoCompra] = useState("unidad");

  const [contenidoPack, setContenidoPack] = useState("");
  const isEditMode = Boolean(editingPedido);
  const lineaSeleccionadaParaEdicion = editingLineKey
    ? productosPedido.find((linea) => linea.clientKey === editingLineKey)
    : undefined;

  const cerrarAlertaEliminar = () => {
    setDeletePedidoAlert({
      isOpen: false,
      pedidoId: null,
      puedeEliminar: false,
    });
  };

  const resetModal = () => {
    setMostrarPedidoView(false);
    setEditingPedido(null);
    setProductosPedido([]);
    setProductoId("");
    setCantidad("");
    setCostoCompra("");
    setContenidoPack("");
    setTipoCompra("unidad");
    setEditingLineKey(null);
    setSelectedProveedor("");
  };

  /* =========================
     FETCH
  ========================= */

  const fetchEntradas = async () => {
    const { data } = await api.get("/inventario/entradas/");
    setEntradas(data.results ?? data);
  };

  const fetchInventario = async () => {
    const { data } = await api.get("/inventario/inventarios/");
    setInventario(data.results ?? data);
  };

  const fetchProveedores = async () => {
    try {
      const res = await api.get("/catalogo/proveedores/");

      const data = res.data.results ?? res.data;
      setProveedores(Array.isArray(data) ? data : []);
    } catch (error) {
      console.error(error);
      toast.error("Error al cargar proveedores");
    }
  };

  useEffect(() => {
    fetchEntradas();
    fetchInventario();
    fetchProveedores();
  }, []);

  useEffect(() => {
    const fetchProductosFiltrados = async () => {
      if (selectedProveedor) {
        try {
          const { data } = await api.get("/catalogo/productos/");
          const allProducts = data.results ?? data;

          // Filtramos en el frontend usando el nuevo campo proveedorId
          const filtered = allProducts.filter(
            (p: any) => p.proveedorId === parseInt(selectedProveedor),
          );

          if (filtered.length > 0) {
            setProductos(filtered);
          } else {
            // Si el maestro quiere restricción estricta, lo dejamos vacío si no hay asignados
            setProductos([]);
            toast.info("Este proveedor no tiene productos asignados.");
          }
        } catch (error) {
          console.error("Error fetching products:", error);
          setProductos([]);
        }
      } else {
        setProductos([]);
      }
    };

    fetchProductosFiltrados();
  }, [selectedProveedor]);

  /* HANDLERS */

  const handleViewPedidoClick = (pedido: any) => {
    setViewingPedido(pedido);
    setIsViewPedidoOpen(true);
  };
  const handleEditPedidoClick = (pedido: any) => {
    setProveedorSeleccionado(null);
    setIsViewPedidoOpen(false);
    setEditingPedido(pedido);
    setEditingLineKey(null);
    setProductoId("");
    setCantidad("");
    setCostoCompra("");
    setContenidoPack("");
    setTipoCompra("unidad");

    // proveedor
    setSelectedProveedor(pedido.proveedorId?.toString());

    // convertir detalles a formato del formulario
    const mapped = pedido.detalles.map((d: DetalleEntradaApi) => {
      const linea = productoPedidoDesdeDetalle(d);
      const pack = unidadesDePack(linea.tipoCompra);

      if (linea.tipoCompra.toLowerCase().startsWith("pack") && !pack) {
        toast.warning(
          `El detalle #${linea.detalleId} usa "${linea.tipoCompra}" sin un tamaño de pack válido. Se conservará sin convertir.`,
        );
      } else if (pack && linea.cantidad % pack !== 0) {
        toast.warning(
          `El detalle #${linea.detalleId} tiene ${linea.cantidad} unidades, no divisibles entre ${pack}. Se conservarán sus valores originales.`,
        );
      }

      return linea;
    });

    setProductosPedido(mapped);

    setMostrarPedidoView(true);
  };

  const handleDeletePedidoClick = async (pedido: any) => {
    try {
      const res = await api.get(
        `/inventario/detalles-entrada/?entradaInventarioId=${pedido.id}`,
      );

      const detalles = res.data.results ?? res.data;

      const puedeEliminar = detalles.every((d: any) =>
        d.estadoItems?.every((i: any) => i.Estado === "Disponible"),
      );

      setDeletePedidoAlert({
        isOpen: true,
        pedidoId: pedido.id,
        puedeEliminar,
      });
    } catch (error) {
      toast.error(mensajeApi(error, "No se pudo comprobar el pedido."));
    }
  };

  const handleConfirmDeletePedido = async () => {
    const entradaId = deletePedidoAlert.pedidoId;

    if (!entradaId || isNaN(Number(entradaId))) {
      toast.error("No se pudo identificar el pedido que se va a eliminar.");
      return;
    }

    if (isDeletingPedido) return;
    setIsDeletingPedido(true);
    try {
      await api.delete(`/inventario/entradas/${entradaId}/delete_completo/`);
      cerrarAlertaEliminar();
      toast.success("Pedido eliminado");
      try {
        await Promise.all([fetchEntradas(), fetchInventario()]);
      } catch {
        toast.warning(
          "El pedido fue eliminado, pero no se pudo actualizar la pantalla. Recarga antes de volver a intentar.",
        );
      }
    } catch (error) {
      toast.error(
        mensajeApi(
          error,
          "No se pudo eliminar el pedido. Puede tener stock utilizado o historial.",
        ),
      );
    } finally {
      setIsDeletingPedido(false);
    }
  };

  /* =========================
     FILTRO
  ========================= */

  const proveedoresFiltrados = proveedores.filter((p) => {
    const t = busqueda.toLowerCase();

    return (
      p.name.toLowerCase().includes(t) ||
      (p.contact ?? "").toLowerCase().includes(t)
    );
  });

  const pedidosProveedor = entradas.filter((e) => {
    if (!proveedorSeleccionado) return false;

    const hace30 = new Date();
    hace30.setDate(hace30.getDate() - 30);

    return (
      e.proveedorId === proveedorSeleccionado.id &&
      new Date(e.fechaEntrada) >= hace30
    );
  });

  /* =========================
     AGREGAR PROVEEDOR
  ========================= */

  const agregarProveedor = async () => {
    if (!nuevoProveedor.name) {
      toast.error("Nombre requerido");
      return;
    }

    try {
      await api.post("/catalogo/proveedores/", nuevoProveedor);

      toast.success("Proveedor agregado");

      setNuevoProveedor({ name: "", contact: "" });
      setModalProveedor(false);
      setMostrarPedidoView(false);

      setSuccessMessage(true); // el useEffect se encarga del timer

      fetchProveedores();
    } catch (e) {
      toast.error("Error al crear proveedor");
    }
  };

  const limpiarEditorLinea = () => {
    setEditingLineKey(null);
    setProductoId("");
    setCantidad("");
    setCostoCompra("");
    setContenidoPack("");
    setTipoCompra("unidad");
  };

  const editarProductoPedido = (linea: ProductoPedido) => {
    const pack = unidadesDePack(linea.tipoCompra);
    if (linea.tipoCompra !== "unidad" && !pack) {
      toast.error(
        `No se puede convertir "${linea.tipoCompra}" de forma segura. El detalle se conservará intacto.`,
      );
      return;
    }

    setEditingLineKey(linea.clientKey);
    setProductoId(linea.productoId.toString());
    setTipoCompra(pack ? "pack" : "unidad");
    setContenidoPack(pack ? pack.toString() : "");
    setCantidad(linea.cantidadMostrada.toString());
    setCostoCompra(
      (pack ? linea.precioUnitario * pack : linea.precioUnitario).toFixed(2),
    );
  };

  const agregarProducto = () => {
    if (!productoId || !cantidad || !costoCompra) return;

    const producto = productos.find((p) => p.id.toString() === productoId);

    if (!producto && !lineaSeleccionadaParaEdicion) return;

    if (
      lineaSeleccionadaParaEdicion?.detalleId !== undefined &&
      Number(productoId) !== lineaSeleccionadaParaEdicion.productoId
    ) {
      toast.error(
        "No se puede cambiar el producto de un detalle existente. Retira la línea y agrega una nueva.",
      );
      return;
    }

    // unidades por pack
    const unidadesPorPack =
      tipoCompra === "pack" ? Number(contenidoPack || 1) : 1;

    const cantidadMostrada = Number(cantidad);
    const costoMostrado = Number(costoCompra);
    if (
      !Number.isInteger(cantidadMostrada) ||
      cantidadMostrada <= 0 ||
      !Number.isInteger(unidadesPorPack) ||
      unidadesPorPack <= 0 ||
      !Number.isFinite(costoMostrado) ||
      costoMostrado < 0
    ) {
      toast.error("Usa cantidades enteras positivas y un costo no negativo.");
      return;
    }

    // cantidad real para inventario
    const cantidadReal = cantidadMostrada * unidadesPorPack;

    // precio unitario real
    const precioUnitarioSinRedondear =
      tipoCompra === "pack"
        ? costoMostrado / unidadesPorPack
        : costoMostrado;
    const precioUnitario = Number(precioUnitarioSinRedondear.toFixed(2));

    // total calculado automáticamente
    const totalPagado = cantidadReal * precioUnitario;

    const tipoCompraTexto =
      tipoCompra === "pack" ? `pack(${contenidoPack})` : "unidad";

    const valoresLinea: Omit<ProductoPedido, "detalleId" | "clientKey"> = {
      productoId: Number(productoId),
      nombre: producto?.name ?? lineaSeleccionadaParaEdicion!.nombre,
      presentacion:
        producto?.presentacion ?? lineaSeleccionadaParaEdicion?.presentacion,

      tipoCompra: tipoCompraTexto,

      // cantidad real inventario
      cantidad: cantidadReal,

      // cantidad escrita por el usuario
      cantidadMostrada: Number(cantidad),

      unidadesPorPack,

      totalPagado,

      precioUnitario,
    };

    if (editingLineKey) {
      setProductosPedido((lineas) =>
        reemplazarLineaPedido(lineas, editingLineKey, valoresLinea),
      );
    } else {
      setProductosPedido((lineas) => [
        ...lineas,
        {
          ...valoresLinea,
          clientKey: `new-${Date.now()}-${lineas.length}`,
        },
      ]);
    }

    limpiarEditorLinea();
  };

  // ELIMINAR PRODUCTO (NUEVO)
  const eliminarProductoPedido = (index: number) => {
    if (productosPedido[index]?.clientKey === editingLineKey) {
      limpiarEditorLinea();
    }
    const updated = productosPedido.filter((_, i) => i !== index);
    setProductosPedido(updated);
  };

  const calcTotal = () =>
    productosPedido.reduce((sum, p) => sum + p.totalPagado, 0);

  const handleSubmit = async () => {
    if (!selectedProveedor || productosPedido.length === 0) {
      toast.error("Completa los campos");
      return;
    }

    if (isSavingPedido) return;
    setIsSavingPedido(true);
    const detalles = construirDetallesPayload(productosPedido);
    const payload = {
      proveedorId: Number(selectedProveedor),
      detalles,
    };

    try {
      if (isEditMode) {
        await api.put(
          `/inventario/entradas/${editingPedido.id}/editar-completa/`,
          payload,
        );
      } else {
        await api.post("/inventario/entradas/crear-completa/", payload);
      }

      toast.success(isEditMode ? "Pedido actualizado" : "Pedido creado");
      try {
        await Promise.all([fetchEntradas(), fetchInventario()]);
      } catch {
        toast.warning(
          "La compra se guardó, pero no se pudo actualizar la pantalla. Recarga antes de volver a intentar.",
        );
      }
      resetModal();
      setIsAddMode(false);
    } catch (error) {
      toast.error(
        mensajeApi(
          error,
          "No se pudo guardar la compra. Revisa cantidades, costos y posibles movimientos de stock.",
        ),
      );
    } finally {
      setIsSavingPedido(false);
    }
  };

  /* =========================
     UI
  ========================= */

  return (
    <div className="space-y-6">
      {/* ======================================================
        VISTA DETALLE PROVEEDOR
    ====================================================== */}
      {proveedorSeleccionado ? (
        <div className="space-y-8">
          {/* HEADER */}
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              className="rounded-2xl"
              onClick={() => setProveedorSeleccionado(null)}
            >
              <ArrowLeft className="size-5" />
            </Button>

            <div>
              <h1 className="text-3xl font-bold tracking-tight">
                {proveedorSeleccionado.name}
              </h1>

              <p className="text-muted-foreground mt-1">
                {pedidosProveedor.length} pedidos en los últimos 30 días
              </p>
            </div>
          </div>

          {/* TABLA */}
          {pedidosProveedor.length === 0 ? (
            <div className="py-20 text-center">
              <p className="text-muted-foreground text-lg">
                No hay pedidos recientes
              </p>
            </div>
          ) : (
            <div className="border rounded-2xl overflow-hidden">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Fecha</TableHead>

                    <TableHead>Total Pagado</TableHead>

                    <TableHead className="text-right">Acciones</TableHead>
                  </TableRow>
                </TableHeader>

                <TableBody>
                  {pedidosProveedor.map((e) => (
                    <TableRow key={e.id}>
                      <TableCell>
                        {new Date(e.fechaEntrada).toLocaleDateString("en-US")}
                      </TableCell>

                      <TableCell className="font-medium">
                        C${Number(e.total).toFixed(2)}
                      </TableCell>

                      <TableCell className="text-right">
                        <div className="flex gap-2 justify-end">
                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Ver pedido ${e.id}`}
                            onClick={(event) => {
                              viewPedidoTriggerRef.current =
                                event.currentTarget;
                              handleViewPedidoClick(e);
                            }}
                          >
                            <Eye className="size-4" />
                          </Button>

                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Editar pedido ${e.id}`}
                            onClick={() => handleEditPedidoClick(e)}
                          >
                            <Pencil className="size-4" />
                          </Button>

                          <Button
                            variant="ghost"
                            size="sm"
                            aria-label={`Eliminar pedido ${e.id}`}
                            onClick={(event) => {
                              deletePedidoTriggerRef.current =
                                event.currentTarget;
                              void handleDeletePedidoClick(e);
                            }}
                          >
                            <Trash2 className="size-4 text-red-500" />
                          </Button>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </div>
      ) : mostrarPedidoView ? (
        <div className="space-y-8">
          {/* HEADER */}
          <div className="flex items-center gap-4">
            <Button
              variant="ghost"
              size="icon"
              className="rounded-2xl"
              disabled={isSavingPedido}
              onClick={() => {
                if (isEditMode) {
                  setIsViewPedidoOpen(false);

                  setProveedorSeleccionado({
                    id: editingPedido.proveedorId,
                    name: editingPedido.proveedorNombre,
                    contact: editingPedido.proveedorContacto ?? null,
                    status: "",
                    pedidos_recientes: 0,
                    activo: true,
                  });

                  setMostrarPedidoView(false);
                } else {
                  resetModal();
                }
              }}
            >
              <ArrowLeft className="size-5" />
            </Button>

            <div>
              <h1 className="text-3xl font-bold tracking-tight">
                {isEditMode ? "Editar compra" : "Añadir compra"}
              </h1>

              <p className="text-muted-foreground mt-1">
                Gestiona productos, cantidades y costos de la compra.
              </p>
            </div>
          </div>

          {/* CONTENEDOR PRINCIPAL */}
          <Card className="p-6 space-y-6 w-full">
            {/* ========================= PROVEEDOR ========================= */}
            <Select
              value={selectedProveedor}
              onValueChange={setSelectedProveedor}
              disabled={productosPedido.length > 0}
            >
              <SelectTrigger className="h-11 w-full">
                <SelectValue placeholder="Proveedor" />
              </SelectTrigger>

              <SelectContent>
                {proveedores
                  .filter(
                    (p) =>
                      p.tiene_productos ||
                      entradas.some((e) => e.proveedorId === p.id),
                  )
                  .map((p) => (
                    <SelectItem key={p.id} value={p.id.toString()}>
                      {p.name}
                    </SelectItem>
                  ))}
              </SelectContent>
            </Select>

            {/* ========================= PRODUCTOS ========================= */}
            <div className="border border-border rounded-lg p-4 space-y-4">
              <h3 className="font-bold">Agregar productos</h3>

              {/* CALCULOS */}
              {(() => {
                const unidadesReales =
                  tipoCompra === "pack"
                    ? Number(cantidad || 0) * Number(contenidoPack || 1)
                    : Number(cantidad || 0);

                const totalCalculado =
                  Number(cantidad || 0) * Number(costoCompra || 0);

                const precioUnitario =
                  tipoCompra === "pack"
                    ? Number(contenidoPack || 0) > 0
                      ? Number(costoCompra || 0) / Number(contenidoPack)
                      : 0
                    : Number(costoCompra || 0);

                return (
                  <>
                    <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                      {/* PRODUCTO */}
                      <Select
                        value={productoId}
                        onValueChange={setProductoId}
                        disabled={
                          !selectedProveedor ||
                          lineaSeleccionadaParaEdicion?.detalleId !== undefined
                        }
                      >
                        <SelectTrigger className="h-11 w-full">
                          <SelectValue
                            placeholder={
                              selectedProveedor
                                ? "Producto"
                                : "Primero elige un proveedor..."
                            }
                          />
                        </SelectTrigger>

                        <SelectContent>
                          {productos.map((p) => (
                            <SelectItem key={p.id} value={p.id.toString()}>
                              {p.name}{" "}
                              {p.presentacion ? `(${p.presentacion})` : ""}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>

                      {/* TIPO COMPRA */}
                      <Select value={tipoCompra} onValueChange={setTipoCompra}>
                        <SelectTrigger className="h-11 w-full">
                          <SelectValue />
                        </SelectTrigger>

                        <SelectContent>
                          <SelectItem value="unidad">Unidad</SelectItem>

                          <SelectItem value="pack">Pack</SelectItem>
                        </SelectContent>
                      </Select>

                      {/* CONTENIDO PACK */}
                      <Input
                        type="number"
                        placeholder="Unidades x pack"
                        value={contenidoPack}
                        disabled={tipoCompra !== "pack"}
                        onChange={(e) => setContenidoPack(e.target.value)}
                        className="h-11"
                      />

                      {/* CANTIDAD */}
                      <Input
                        type="number"
                        placeholder={
                          tipoCompra === "pack" ? "Cantidad packs" : "Cantidad"
                        }
                        value={cantidad}
                        onChange={(e) => setCantidad(e.target.value)}
                        className="h-11"
                      />

                      {/* TOTAL */}
                      <Input
                        type="number"
                        placeholder={
                          tipoCompra === "pack"
                            ? "Costo por pack"
                            : "Costo por unidad"
                        }
                        value={costoCompra}
                        onChange={(e) => setCostoCompra(e.target.value)}
                      />

                      {/* UNITARIO */}
                      <Input
                        disabled
                        className="h-11 bg-muted"
                        value={`C$${precioUnitario.toFixed(2)}`}
                      />
                    </div>

                    <div className="flex gap-2">
                      <Button
                        onClick={agregarProducto}
                        disabled={isSavingPedido}
                      >
                        {editingLineKey ? (
                          <Pencil className="size-4 mr-2" />
                        ) : (
                          <Plus className="size-4 mr-2" />
                        )}
                        {editingLineKey
                          ? "Actualizar línea"
                          : "Agregar a la compra"}
                      </Button>
                      {editingLineKey && (
                        <Button
                          variant="outline"
                          onClick={limpiarEditorLinea}
                          disabled={isSavingPedido}
                        >
                          Cancelar edición
                        </Button>
                      )}
                    </div>
                  </>
                );
              })()}
            </div>

            {/* ========================= LISTA ========================= */}
            {productosPedido.length > 0 && (
              <div className="border rounded-lg p-4 space-y-3">
                <h3 className="font-bold">Productos del pedido</h3>

                {productosPedido.map((p, i) => (
                  <div
                    key={p.clientKey}
                    className="flex justify-between items-center bg-muted p-3 rounded"
                  >
                    <div>
                      <p className="font-semibold">
                        {p.nombre} {p.presentacion ? `(${p.presentacion})` : ""}
                      </p>

                      <p className="text-sm text-muted-foreground">
                        {p.tipoCompra === "unidad"
                          ? `${p.cantidad} unidades`
                          : `${p.cantidadMostrada} ${p.tipoCompra}`}
                      </p>
                    </div>

                    <div className="text-right">
                      <p>C${p.totalPagado}</p>

                      <p className="text-xs text-muted-foreground">
                        C${p.precioUnitario.toFixed(2)} unit
                      </p>
                    </div>

                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        onClick={() => editarProductoPedido(p)}
                        disabled={isSavingPedido}
                        aria-label={`Editar detalle ${p.nombre}`}
                      >
                        <Pencil className="size-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        onClick={() => eliminarProductoPedido(i)}
                        disabled={isSavingPedido}
                        aria-label={`Retirar detalle ${p.nombre}`}
                      >
                        <Trash2 className="size-4 text-red-500" />
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {/* ========================= TOTAL ========================= */}
            <div className="flex justify-between border-t pt-4">
              <p className="font-bold">Total: C${calcTotal().toFixed(2)}</p>

              <div className="flex gap-3">
                <Button
                  variant="outline"
                  onClick={resetModal}
                  disabled={isSavingPedido}
                >
                  Cancelar
                </Button>

                <Button onClick={handleSubmit} disabled={isSavingPedido}>
                  <Check className="size-4 mr-2" />

                  {isSavingPedido
                    ? "Guardando..."
                    : isEditMode
                      ? "Actualizar compra"
                      : "Guardar compra"}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      ) : (
        <>
          {/* ======================================================
            VISTA NORMAL
        ====================================================== */}

          {/* HEADER */}
          <div className="flex justify-between items-center">
            <div>
              <h1 className="text-2xl font-bold text-foreground">
                Proveedores
              </h1>

              <p className="text-muted-foreground">
                Gestión de proveedores y actividad reciente
              </p>
            </div>

            <div className="flex gap-3">
              <Button
                onClick={(event) => {
                  proveedorDialogTriggerRef.current = event.currentTarget;
                  setModalProveedor(true);
                }}
                className="
                bg-gradient-to-r
                from-blue-600
                to-blue-800
                hover:from-blue-700
                hover:to-blue-900
                text-white
                shadow-md
              "
              >
                <Plus className="size-4 mr-2" />
                Agregar Proveedor
              </Button>

              <Button
                onClick={() => {
                  resetModal();
                  setMostrarPedidoView(true);
                  setIsViewPedidoOpen(false);
                }}
                className="
    bg-gradient-to-r
    from-green-500
    to-green-700
    hover:from-green-600
    hover:to-green-800
    text-white
    shadow-md
  "
              >
                <Plus className="size-4 mr-2" />
                Nueva Compra
              </Button>
            </div>
          </div>

          {/* SUCCESS */}
          {successMessage && (
            <Card className="p-4 bg-green-50 border border-green-200">
              <div className="flex items-center gap-2">
                <Check className="text-green-600" />

                <p className="text-green-700 font-medium">
                  Proveedor creado correctamente
                </p>
              </div>
            </Card>
          )}

          {/* SEARCH */}
          <Card className="p-4">
            <div className="relative">
              <Search className="absolute left-3 top-3 text-gray-400 size-5" />

              <Input
                placeholder="Buscar proveedor..."
                className="pl-10"
                value={busqueda}
                onChange={(e) => setBusqueda(e.target.value)}
              />
            </div>
          </Card>

          {/* LISTA */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {proveedoresFiltrados.map((p) => (
              <Card
                key={p.id}
                onClick={() => setProveedorSeleccionado(p)}
                className="
                p-4
                space-y-2
                hover:shadow-md
                transition
                cursor-pointer
              "
              >
                {/* HEADER */}
                <div className="flex justify-between items-center">
                  <div className="flex items-center gap-2">
                    <Store className="text-foreground" />

                    <p className="font-bold text-foreground">{p.name}</p>
                  </div>

                  <span
                    className={`size-3 rounded-full ${
                      p.activo ? "bg-green-500" : "bg-gray-300"
                    }`}
                  />
                </div>

                {/* CONTACTO */}
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Phone className="size-3.5" />

                  {p.contact ?? "Sin contacto"}
                </div>

                {/* PEDIDOS */}
                <p className="text-xs text-muted-foreground">
                  Pedidos recientes (últimos 30 días):
                  <span className="font-semibold ml-1">
                    {p.pedidos_recientes}
                  </span>
                </p>
              </Card>
            ))}
          </div>
        </>
      )}

      {/* ======================================================
        MODALES
    ====================================================== */}

      {/* MODAL VIEW PEDIDO */}
      <Dialog open={isViewPedidoOpen} onOpenChange={setIsViewPedidoOpen}>
        <DialogContent
          className="max-w-2xl"
          onCloseAutoFocus={(event) => {
            const trigger = viewPedidoTriggerRef.current;
            if (trigger?.isConnected) {
              event.preventDefault();
              trigger.focus();
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>Detalle del Pedido</DialogTitle>
            <DialogDescription>
              Información de la fecha, el proveedor, los productos y los
              importes de la compra.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <p>
              <b>Fecha:</b>{" "}
              {new Date(viewingPedido?.fechaEntrada).toLocaleDateString(
                "es-NI",
              )}
            </p>

            <p>
              <b>Proveedor:</b> {viewingPedido?.proveedorNombre}
            </p>

            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Producto</TableHead>
                  <TableHead>Cantidad</TableHead>
                  <TableHead>Precio Unitario</TableHead>
                  <TableHead>Subtotal</TableHead>
                </TableRow>
              </TableHeader>

              <TableBody>
                {viewingPedido?.detalles?.map((d: any) => {
                  const subtotal =
                    Number(d.cantidad) * Number(d.precioCompraUnitario);

                  return (
                    <TableRow key={d.id}>
                      <TableCell>
                        {d.productoNombre}{" "}
                        {d.productoPresentacion
                          ? `(${d.productoPresentacion})`
                          : ""}
                      </TableCell>

                      <TableCell>
                        {d.tipoCompra === "unidad"
                          ? `${d.cantidad} unit`
                          : `${d.cantidad / Number(d.tipoCompra.match(/\((\d+)\)/)?.[1] || 1)} ${d.tipoCompra}`}
                      </TableCell>

                      <TableCell>
                        C$
                        {Number(d.precioCompraUnitario).toFixed(2)}
                      </TableCell>

                      <TableCell>C${subtotal.toFixed(2)}</TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>

            <p className="text-right font-bold">
              Total: C$
              {Number(viewingPedido?.total || 0).toFixed(2)}
            </p>
          </div>
        </DialogContent>
      </Dialog>

      {/* MODAL ALERTA ELIMINAR PEDIDO */}
      <AlertDialog
        open={deletePedidoAlert.isOpen}
        onOpenChange={(open) => {
          if (!open && !isDeletingPedido) cerrarAlertaEliminar();
        }}
      >
        <AlertDialogContent
          onEscapeKeyDown={(event) => {
            if (isDeletingPedido) event.preventDefault();
          }}
          onCloseAutoFocus={(event) => {
            const trigger = deletePedidoTriggerRef.current;
            if (trigger?.isConnected) {
              event.preventDefault();
              trigger.focus();
            }
          }}
        >
          <AlertDialogHeader>
            <AlertDialogTitle>
              {deletePedidoAlert.puedeEliminar
                ? "¿Eliminar pedido?"
                : "No se puede eliminar"}
            </AlertDialogTitle>

            <AlertDialogDescription>
              {deletePedidoAlert.puedeEliminar
                ? "Se eliminará el pedido completo."
                : "Todos los items deben estar disponibles."}
            </AlertDialogDescription>
          </AlertDialogHeader>

          <AlertDialogFooter>
            {deletePedidoAlert.puedeEliminar ? (
              <>
                <AlertDialogCancel disabled={isDeletingPedido}>
                  Cancelar
                </AlertDialogCancel>
                <Button
                  onClick={handleConfirmDeletePedido}
                  disabled={isDeletingPedido}
                >
                  {isDeletingPedido ? "Eliminando..." : "Sí, eliminar"}
                </Button>
              </>
            ) : (
              <AlertDialogCancel>
                Entendido
              </AlertDialogCancel>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {/* =========================
          MODAL PROVEEDOR
      ========================= */}
      <Dialog open={modalProveedor} onOpenChange={setModalProveedor}>
        <DialogContent
          onCloseAutoFocus={(event) => {
            const trigger = proveedorDialogTriggerRef.current;
            if (trigger?.isConnected) {
              event.preventDefault();
              trigger.focus();
            }
          }}
        >
          <DialogHeader>
            <DialogTitle>Agregar Proveedor</DialogTitle>
            <DialogDescription>
              Ingresa el nombre y el número de contacto del nuevo proveedor.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <Label htmlFor="nombre">Nombre del proveedor</Label>
              <Input
                id="nombre"
                value={nuevoProveedor.name}
                onChange={(e) =>
                  setNuevoProveedor({
                    ...nuevoProveedor,
                    name: e.target.value,
                  })
                }
                placeholder="Ej: Distribuidora ABC"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contacto">Número de contacto</Label>
              <Input
                id="contacto"
                value={nuevoProveedor.contact}
                onChange={(e) =>
                  setNuevoProveedor({
                    ...nuevoProveedor,
                    contact: e.target.value,
                  })
                }
                placeholder="Ej: 8888-9999"
              />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setModalProveedor(false)}>
              Cancelar
            </Button>
            <Button onClick={agregarProveedor}>Agregar</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
