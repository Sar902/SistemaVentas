import {
  Plus,
  Search,
  Check,
  Loader2,
  Calendar,
  CornerDownLeft,
  AlertCircle,
  Eye,
  XCircle,
  CheckCircle2
} from "lucide-react";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Card } from "../components/ui/card";
import { useState, useEffect, useMemo, useRef } from "react";
import api from "../api/axiosInstance";
import { toast } from "sonner";
import {
  agruparInventarioDevolucion,
  construirPayloadDevolucion,
  construirResolucionDevolucion,
  estadoDeSolicitud,
  type EstadoResolucion,
} from "./Devoluciones.helpers";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "../components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "../components/ui/dialog";
import { Label } from "../components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../components/ui/select";
import { Textarea } from "../components/ui/textarea";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "../components/ui/alert-dialog";

export function Devoluciones() {
  const [devoluciones, setDevoluciones] = useState<any[]>([]);
  const [inventarioDisponible, setInventarioDisponible] = useState<any[]>([]);
  const [productos, setProductos] = useState<any[]>([]);
  const [detallesEntrada, setDetallesEntrada] = useState<any[]>([]);

  const [searchTerm, setSearchTerm] = useState("");
  const [isAddOpen, setIsAddOpen] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Detalles modal states
  const [selectedDevolucion, setSelectedDevolucion] = useState<any>(null);
  const [isDetailOpen, setIsDetailOpen] = useState(false);
  const [isUpdatingState, setIsUpdatingState] = useState(false);
  const [pendingResolution, setPendingResolution] =
    useState<EstadoResolucion | null>(null);

  //Form states
  const [motivo, setMotivo] = useState("");
  const [observaciones, setObservaciones] = useState(""); // Nuevo campo para la cabecera
  const [selectedGroupKey, setSelectedGroupKey] = useState<string>("");
  const [cantidadADevolver, setCantidadADevolver] = useState<number>(1);

  const createInFlight = useRef(false);
  const resolutionInFlight = useRef(false);

  useEffect(() => {
    void fetchData().catch(() => {
      toast.error("Error al cargar datos.");
    });
  }, []);

  const fetchData = async (includeLosses = false) => {
    const requests = [
      api.get("/inventario/solicitudes-devolucion/"),
      api.get("/inventario/inventarios/"),
      api.get("/catalogo/productos/"),
      api.get("/inventario/detalles-entrada/"),
    ];
    if (includeLosses) {
      requests.push(api.get("/inventario/perdidas/"));
    }
    const [devolucionesRes, invRes, prodRes, detRes] = await Promise.all(requests);
    const devoluciones = devolucionesRes.data.results ?? devolucionesRes.data;
    const inventario = invRes.data.results ?? invRes.data;
    const productos = prodRes.data.results ?? prodRes.data;
    const detalles = detRes.data.results ?? detRes.data;

    setDevoluciones(Array.isArray(devoluciones) ? devoluciones : []);
    setProductos(Array.isArray(productos) ? productos : []);
    setDetallesEntrada(Array.isArray(detalles) ? detalles : []);
    setInventarioDisponible(Array.isArray(inventario) ? inventario : []);
  };

  const availableGroups = useMemo(
    () =>
      agruparInventarioDevolucion(
        inventarioDisponible,
        detallesEntrada,
        productos,
      ),
    [inventarioDisponible, detallesEntrada, productos],
  );

  const handleSave = async () => {
    if (createInFlight.current) return;

    if (!selectedGroupKey || !motivo) {
      toast.warning("Debes seleccionar un ítem y especificar un motivo.");
      return;
    }

    if (motivo.trim().length < 15) {
      toast.warning(
        "Por favor, detalla mejor el motivo de rechazo (mínimo 15 caracteres).",
      );
      return;
    }

    const group = availableGroups.find(g => g.key === selectedGroupKey);

    if (!group) {
      toast.error("El grupo de productos seleccionado no es válido.");
      return;
    }

    if (group.items.length < cantidadADevolver || cantidadADevolver < 1) {
      toast.error("Cantidad inválida o superior al stock disponible.");
      return;
    }

    createInFlight.current = true;
    setIsSubmitting(true);
    try {
      const payload = construirPayloadDevolucion(
        group,
        cantidadADevolver,
        motivo,
        observaciones,
      );

      await api.post("/inventario/procesar-devolucion/", payload);
      setIsAddOpen(false);
      setSelectedGroupKey("");
      setCantidadADevolver(1);
      setMotivo("");
      setObservaciones("");
      try {
        await fetchData();
        toast.success("Solicitud de devolución enviada al proveedor.");
      } catch {
        toast.warning(
          "La solicitud se creó, pero no se pudo actualizar la pantalla.",
        );
      }
    } catch (e: any) {
      toast.error(
        e.response?.data?.error ||
        e.response?.data?.detail ||
        "No se pudo crear la solicitud de devolución.",
      );
    } finally {
      createInFlight.current = false;
      setIsSubmitting(false);
    }
  };

  const handleViewDetails = (devolucion: any) => {
    setSelectedDevolucion(devolucion);
    setIsDetailOpen(true);
  };

  const handleChangeState = async () => {
    if (!selectedDevolucion || !pendingResolution || resolutionInFlight.current) {
      return;
    }
    if (!estadoDeSolicitud(selectedDevolucion).puedeResolver) return;

    resolutionInFlight.current = true;
    setIsUpdatingState(true);
    try {
      const id = selectedDevolucion.id || selectedDevolucion.IdSolicitudDevolucion;
      const resolucion = construirResolucionDevolucion(id, pendingResolution);
      await api.post(resolucion.url, resolucion.payload);

      const estadoAplicado = pendingResolution;
      setPendingResolution(null);
      setIsDetailOpen(false);
      setSelectedDevolucion(null);
      try {
        await fetchData(estadoAplicado === "Rechazada");
        toast.success(
          estadoAplicado === "Aceptada"
            ? "Devolución aceptada; las unidades permanecen fuera del stock disponible."
            : "Devolución rechazada y unidades registradas como pérdida.",
        );
      } catch {
        toast.warning(
          "La devolución se resolvió, pero no se pudo actualizar la pantalla.",
        );
      }
    } catch (error: any) {
      toast.error(
        error.response?.data?.error || "No se pudo resolver la devolución.",
      );
    } finally {
      resolutionInFlight.current = false;
      setIsUpdatingState(false);
    }
  };

  const filtered = devoluciones.filter(
    (d) =>
      d.id?.toString().includes(searchTerm) ||
      d.IdSolicitudDevolucion?.toString().includes(searchTerm) ||
      (d.Observaciones &&
        d.Observaciones.toLowerCase().includes(searchTerm.toLowerCase())),
  );
  const selectedState = selectedDevolucion
    ? estadoDeSolicitud(selectedDevolucion)
    : null;

  return (
    <div className="space-y-6 max-w-7xl mx-auto">
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold text-foreground">
            Devoluciones a Proveedores
          </h1>
          <p className="text-muted-foreground">
            Gestión de productos defectuosos sujetos a devolución
          </p>
        </div>
        <Button
          onClick={() => setIsAddOpen(true)}
          className="bg-gradient-to-r from-slate-700 to-slate-900 dark:from-slate-500 dark:to-slate-700 hover:from-slate-800 hover:to-slate-950 text-white shadow-md"
        >
          <Plus className="size-4 mr-2" />
          Nueva Solicitud
        </Button>
      </div>

      <Card className="p-6 border-0 shadow-lg">
        <div className="mb-6 flex">
          <div className="relative max-w-md w-full">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 size-5 text-gray-400" />
            <Input
              placeholder="Buscar por ID..."
              className="pl-10 h-11"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
            />
          </div>
        </div>

        <div className="rounded-lg border overflow-hidden">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>ID Solicitud</TableHead>
                <TableHead>Producto</TableHead>
                <TableHead>Cant.</TableHead>
                <TableHead>Motivo</TableHead>
                <TableHead>Fecha</TableHead>
                <TableHead>Estado</TableHead>
                <TableHead>Usuario</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.length > 0 ? (
                filtered.map((d) => {
                  const idSol = d.id || d.IdSolicitudDevolucion;
                  const estado = estadoDeSolicitud(d).valor;
                  const primerDetalle = d.detalles?.[0];

                  return (
                    <TableRow
                      key={idSol}
                      className="hover:bg-muted transition-colors cursor-pointer"
                      onClick={() => handleViewDetails(d)}
                    >
                      <TableCell className="font-medium text-foreground">
                        #{idSol?.toString().padStart(4, "0")}
                      </TableCell>
                      <TableCell className="max-w-[150px] truncate" title={primerDetalle?.productoNombre}>
                        {primerDetalle?.productoNombre || "N/A"}
                      </TableCell>
                      <TableCell>
                        {d.detalles?.length || 0}
                      </TableCell>
                      <TableCell className="max-w-[150px] truncate" title={primerDetalle?.motivoRechazo}>
                        {primerDetalle?.motivoRechazo || "Sin motivo"}
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-2 text-muted-foreground">
                          <Calendar className="size-4" />
                          {new Date(d.fecha || d.Fecha).toLocaleDateString("es-ES")}
                        </div>
                      </TableCell>
                      <TableCell>
                        <span
                          className={`px-2 py-1 text-xs rounded-full font-semibold ${estado === "Aceptada" || estado === "Aprobada"
                            ? "bg-green-100 text-green-800"
                            : estado === "Rechazada"
                              ? "bg-red-100 text-red-800"
                              : "bg-amber-100 text-amber-800"
                            }`}
                        >
                          {estado}
                        </span>
                      </TableCell>
                      <TableCell className="text-muted-foreground font-medium">
                        {d.usuarioNombre || `ID: ${d.usuarioId || d.IdUsuario}`}
                      </TableCell>
                    </TableRow>
                  );
                })
              ) : (
                <TableRow>
                  <TableCell
                    colSpan={7}
                    className="text-center py-8 text-muted-foreground"
                  >
                    No se encontraron registros de devoluciones.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </Card>

      {/* MODAL DETALLE DEVOLUCION */}
      <Dialog
        open={isDetailOpen}
        onOpenChange={(open) => {
          if (isUpdatingState) return;
          setIsDetailOpen(open);
          if (!open) setSelectedDevolucion(null);
        }}
      >
        <DialogContent
          className="sm:max-w-[500px]"
          aria-labelledby="detalle-devolucion-titulo"
          aria-describedby="detalle-devolucion-descripcion"
        >
          <DialogHeader>
            <DialogTitle
              id="detalle-devolucion-titulo"
              className="flex items-center gap-2 text-foreground"
            >
              <Eye className="size-5" />
              Detalle de Devolución
            </DialogTitle>
            <DialogDescription id="detalle-devolucion-descripcion">
              Información de la solicitud #{selectedDevolucion?.id || selectedDevolucion?.IdSolicitudDevolucion}
            </DialogDescription>
          </DialogHeader>
          {selectedDevolucion && (
            <div className="space-y-4 pt-4">
              <div className="grid grid-cols-2 gap-4 text-sm">
                <div>
                  <span className="font-semibold text-muted-foreground block">Producto:</span>
                  <span className="text-foreground">{selectedDevolucion.detalles?.[0]?.productoNombre || "N/A"} {selectedDevolucion.detalles?.[0]?.productoPresentacion ? `(${selectedDevolucion.detalles?.[0]?.productoPresentacion})` : ''}</span>
                </div>
                <div>
                  <span className="font-semibold text-muted-foreground block">Cantidad:</span>
                  <span className="text-foreground">{selectedDevolucion.detalles?.length || 0}</span>
                </div>
                <div className="col-span-2">
                  <span className="font-semibold text-muted-foreground block">Motivo de solicitud:</span>
                  <p className="text-foreground bg-muted p-2 rounded border mt-1">
                    {selectedDevolucion.detalles?.[0]?.motivoRechazo || "Sin motivo registrado"}
                  </p>
                </div>
                <div>
                  <span className="font-semibold text-muted-foreground block">Fecha de Solicitud:</span>
                  <span className="text-foreground">
                    {new Date(selectedDevolucion.fecha || selectedDevolucion.Fecha).toLocaleString("es-ES")}
                  </span>
                </div>
                <div>
                  <span className="font-semibold text-muted-foreground block">Estado Actual:</span>
                  <span className={`px-2 py-1 mt-1 inline-block text-xs rounded-full font-semibold ${selectedState?.valor === "Aceptada" || selectedState?.valor === "Aprobada"
                    ? "bg-green-100 text-green-800"
                    : selectedState?.valor === "Rechazada"
                      ? "bg-red-100 text-red-800"
                      : "bg-amber-100 text-amber-800"
                    }`}>
                    {selectedState?.valor}
                  </span>
                </div>
                <div>
                  <span className="font-semibold text-muted-foreground block">Registrado por:</span>
                  <span className="text-foreground">{selectedDevolucion.usuarioNombre || "N/A"}</span>
                </div>
              </div>

              {selectedState?.puedeResolver ? (
                <div className="flex justify-end gap-3 mt-6 pt-4 border-t">
                  <Button
                    variant="outline"
                    className="border-red-200 text-red-600 hover:bg-red-50"
                    onClick={() => setPendingResolution("Rechazada")}
                    disabled={isUpdatingState}
                  >
                    {isUpdatingState ? <Loader2 className="animate-spin size-4 mr-2" /> : <XCircle className="size-4 mr-2" />}
                    Rechazar
                  </Button>
                  <Button
                    className="bg-green-600 hover:bg-green-700 text-white"
                    onClick={() => setPendingResolution("Aceptada")}
                    disabled={isUpdatingState}
                  >
                    {isUpdatingState ? <Loader2 className="animate-spin size-4 mr-2" /> : <CheckCircle2 className="size-4 mr-2" />}
                    Aprobar devolución
                  </Button>
                </div>
              ) : (
                <div className="bg-muted border p-3 rounded-md mt-6 text-center text-sm text-muted-foreground">
                  Esta devolución ya fue procesada y no puede ser modificada.
                </div>
              )}
            </div>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog
        open={pendingResolution !== null}
        onOpenChange={(open) => {
          if (!open && !isUpdatingState) setPendingResolution(null);
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {pendingResolution === "Aceptada"
                ? "Aceptar devolución"
                : "Rechazar devolución"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {pendingResolution === "Aceptada"
                ? "Al aceptar, las unidades permanecerán devueltas al proveedor y fuera del stock disponible."
                : "Al rechazar, las unidades se registrarán como una pérdida y no volverán al stock disponible."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel
              disabled={isUpdatingState}
              onClick={() => setPendingResolution(null)}
            >
              Cancelar
            </AlertDialogCancel>
            <AlertDialogAction
              disabled={isUpdatingState}
              onClick={(event) => {
                event.preventDefault();
                void handleChangeState();
              }}
              className={
                pendingResolution === "Rechazada"
                  ? "bg-red-600 text-white hover:bg-red-700"
                  : "bg-green-600 text-white hover:bg-green-700"
              }
            >
              {isUpdatingState && (
                <Loader2 className="animate-spin size-4 mr-2" />
              )}
              Confirmar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog
        open={isAddOpen}
        onOpenChange={(open) => {
          if (!isSubmitting) setIsAddOpen(open);
        }}
      >
        <DialogContent
          className="sm:max-w-[425px]"
          aria-labelledby="crear-devolucion-titulo"
          aria-describedby="crear-devolucion-descripcion"
        >
          <DialogHeader>
            <DialogTitle
              id="crear-devolucion-titulo"
              className="flex items-center gap-2 text-foreground"
            >
              <CornerDownLeft className="size-5" />
              Solicitar Devolución
            </DialogTitle>
            <DialogDescription
              id="crear-devolucion-descripcion"
              className="sr-only"
            >
              Selecciona el producto, indica la cantidad y el motivo de rechazo.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 pt-4">
            <div className="bg-amber-50 border-l-4 border-amber-500 p-3 rounded-md mb-2">
              <div className="flex items-start">
                <AlertCircle className="size-5 text-amber-500 mr-2 mt-0.5" />
                <p className="text-xs text-amber-700">
                  Los productos aquí listados son los que aún tienen stock en inventario. La solicitud
                  quedará como Pendiente para el proveedor y el stock se retendrá.
                </p>
              </div>
            </div>

            <div className="space-y-2">
              <Label>Producto</Label>
              <Select
                value={selectedGroupKey}
                disabled={isSubmitting}
                onValueChange={(val) => {
                  setSelectedGroupKey(val);
                  setCantidadADevolver(1); // Resetear cantidad al cambiar de producto
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Selecciona un producto..." />
                </SelectTrigger>
                <SelectContent>
                  {availableGroups.map((group) => (
                    <SelectItem key={group.key} value={group.key}>
                      {group.nombre} {group.presentacion ? `(${group.presentacion})` : ""} - Compra #{group.entradaId}, detalle #{group.detalleId} ({group.items.length} disp.)
                    </SelectItem>
                  ))}
                  {availableGroups.length === 0 && (
                    <div className="p-2 text-sm text-muted-foreground text-center">
                      No hay productos disponibles para devolver.
                    </div>
                  )}
                </SelectContent>
              </Select>
            </div>

            {selectedGroupKey && (
              <div className="space-y-2">
                <Label>Cantidad a Devolver</Label>
                <Input
                  type="number"
                  min={1}
                  max={availableGroups.find(g => g.key === selectedGroupKey)?.items.length || 1}
                  value={cantidadADevolver}
                  disabled={isSubmitting}
                  onChange={(e) => setCantidadADevolver(parseInt(e.target.value) || 1)}
                />
              </div>
            )}

            <div className="space-y-2">
              <Label>Motivo de solicitud</Label>
              <Textarea
                placeholder="Ej. El producto venía vencido..."
                value={motivo}
                disabled={isSubmitting}
                onChange={(e) => setMotivo(e.target.value)}
              />
              <p className="text-xs text-muted-foreground text-right">
                {motivo.length}/15 min.
              </p>
            </div>

            <div className="space-y-2">
              <Label>Observaciones Generales (Detalles)</Label>
              <Input
                placeholder="Notas adicionales para la solicitud..."
                value={observaciones}
                disabled={isSubmitting}
                onChange={(e) => setObservaciones(e.target.value)}
              />
            </div>

            <div className="flex justify-end gap-3 mt-6">
              <Button
                variant="outline"
                disabled={isSubmitting}
                onClick={() => setIsAddOpen(false)}
              >
                Cancelar
              </Button>
              <Button
                disabled={
                  isSubmitting || !selectedGroupKey || motivo.length < 15 || cantidadADevolver < 1
                }
                onClick={handleSave}
                className="bg-slate-800 hover:bg-slate-900 text-white dark:bg-slate-700 dark:hover:bg-slate-600"
              >
                {isSubmitting ? (
                  <Loader2 className="animate-spin size-4 mr-2" />
                ) : (
                  <Check className="size-4 mr-2" />
                )}
                Crear Solicitud
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
