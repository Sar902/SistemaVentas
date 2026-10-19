import { Card } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { useState, useEffect } from "react";
import api from "../api/axiosInstance";
import { toast } from "sonner";
import { Loader2, Save, Eye, EyeOff, Mail, User, KeyRound, ShieldCheck, ShoppingCart, Package, Warehouse, TrendingDown, RefreshCcw, BarChart3, Users, Check, Clock } from "lucide-react";
import { useAuth } from "../contexts/AuthContext";

export function Ajustes() {
  const { userId, userRole } = useAuth();

  // DATOS DEL USUARIO
  const [userData, setUserData] = useState<any>(null);
  const [isLoadingData, setIsLoadingData] = useState(true);

  // PASSWORD
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [passwordErrors, setPasswordErrors] = useState<string[]>([]);
  const [showPassword, setShowPassword] = useState(false);
  const [isSubmittingPassword, setIsSubmittingPassword] = useState(false);

  // EMAIL
  const [email, setEmail] = useState("");
  const [emailError, setEmailError] = useState("");
  const [isSubmittingEmail, setIsSubmittingEmail] = useState(false);
  const [empleados, setEmpleados] = useState<any[]>([]);
  const [empleadoId, setEmpleadoId] = useState("");
  const [duracionHoras, setDuracionHoras] = useState("1");
  const [codigoGenerado, setCodigoGenerado] = useState("");
  const [codigoAdmin, setCodigoAdmin] = useState("");
  const [modulos, setModulos] = useState<string[]>(["VENTAS"]);
  const [delegaciones, setDelegaciones] = useState<any[]>([]);
  const modulosDisponibles = [
    { key: "VENTAS", label: "Ventas", desc: "Caja, cobro y facturación", icon: ShoppingCart, color: "text-blue-500", bgActive: "bg-blue-500/10 border-blue-500/50" },
    { key: "PRODUCTOS", label: "Productos", desc: "Catálogo, precios y stock", icon: Package, color: "text-amber-500", bgActive: "bg-amber-500/10 border-amber-500/50" },
    { key: "INVENTARIO", label: "Inventario y Compras", desc: "Entradas y proveedores", icon: Warehouse, color: "text-emerald-500", bgActive: "bg-emerald-500/10 border-emerald-500/50" },
    { key: "PERDIDAS", label: "Pérdidas", desc: "Registro de mermas y bajas", icon: TrendingDown, color: "text-rose-500", bgActive: "bg-rose-500/10 border-rose-500/50" },
    { key: "DEVOLUCIONES", label: "Devoluciones", desc: "Reingreso y reembolsos", icon: RefreshCcw, color: "text-indigo-500", bgActive: "bg-indigo-500/10 border-indigo-500/50" },
    { key: "REPORTES", label: "Reportes", desc: "Estadísticas y finanzas", icon: BarChart3, color: "text-purple-500", bgActive: "bg-purple-500/10 border-purple-500/50" },
    { key: "AUDITORIA", label: "Auditoría", desc: "Registro de operaciones", icon: ShieldCheck, color: "text-cyan-500", bgActive: "bg-cyan-500/10 border-cyan-500/50" },
    { key: "USUARIOS", label: "Usuarios", desc: "Gestión de cuentas y roles", icon: Users, color: "text-teal-500", bgActive: "bg-teal-500/10 border-teal-500/50" },
  ];

  // CARGAR DATOS DEL USUARIO
  const fetchUserData = async () => {
    try {
      setIsLoadingData(true);
      const response = await api.get(`/usuarios/usuarios/${userId}/`);
      setUserData(response.data);
    } catch (error) {
      console.error("Error al obtener datos del usuario:", error);
      toast.error("No se pudieron cargar los datos del perfil");
    } finally {
      setIsLoadingData(false);
    }
  };

  useEffect(() => {
    if (userId) {
      fetchUserData();
    }
  }, [userId]);

  useEffect(() => {
    if (userRole === "admin") {
      api.get("/usuarios/usuarios/").then(({ data }) => {
        const lista = Array.isArray(data) ? data : data.results || [];
        setEmpleados(lista.filter((empleado: any) => {
          const rol = String(empleado.rol || "").trim().toLowerCase();
          const estado = String(empleado.estado || "").trim().toLowerCase();
          const esCajero = rol === "cajero" || rol === "vendedor";
          const esActivo = estado === "activo" || empleado.is_active === true;
          return esCajero && esActivo;
        }));
      }).catch(() => toast.error("No se pudieron cargar los cajeros."));
      cargarDelegaciones();
    }
  }, [userRole]);

  const cargarDelegaciones = async () => {
    try {
      const { data } = await api.get("/usuarios/delegacion-admin/");
      setDelegaciones(Array.isArray(data) ? data : []);
    } catch {
      toast.error("No se pudieron cargar las delegaciones.");
    }
  };

  const generarCodigoAdmin = async () => {
    if (!empleadoId) {
      toast.warning("Selecciona un cajero.");
      return;
    }
    if (modulos.length === 0) {
      toast.warning("Selecciona al menos un módulo.");
      return;
    }
    try {
      const { data } = await api.post("/usuarios/delegacion-admin/crear/", {
        empleadoId: Number(empleadoId),
        duracionHoras: Number(duracionHoras),
        modulos,
      });
      setCodigoGenerado(data.codigo);
      cargarDelegaciones();
      toast.success("Código generado. Compártelo con el cajero.");
    } catch (error: any) {
      toast.error(getErrorMessage(error));
    }
  };

  const revocarDelegacion = async (id: number) => {
    try {
      await api.post(`/usuarios/delegacion-admin/${id}/revocar/`);
      toast.success("Acceso revocado.");
      cargarDelegaciones();
    } catch (error: any) {
      toast.error(getErrorMessage(error));
    }
  };


  const activarCodigoAdmin = async () => {
    if (!codigoAdmin.trim()) {
      toast.warning("Ingresa el código.");
      return;
    }
    try {
      await api.post("/usuarios/delegacion-admin/activar/", { codigo: codigoAdmin });
      toast.success("Permisos administrativos activados.");
      window.location.reload();
    } catch (error: any) {
      toast.error(getErrorMessage(error));
    }
  };

  // VALIDACIÓN PASSWORD
  const validatePassword = (pwd: string) => {
    const errors: string[] = [];
    if (pwd.length < 8) errors.push("Mínimo 8 caracteres");
    if (!/[A-Z]/.test(pwd)) errors.push("Una mayúscula");
    if (!/[a-z]/.test(pwd)) errors.push("Una minúscula");
    if (!/[0-9]/.test(pwd)) errors.push("Un número");
    if (!/[^A-Za-z0-9]/.test(pwd)) errors.push("Un carácter especial");
    return errors;
  };

  const getErrorMessage = (error: any): string => {
    const data = error.response?.data;
    if (!data) return "Error inesperado";

    const firstKey = Object.keys(data)[0];
    if (Array.isArray(data[firstKey])) {
      return data[firstKey][0];
    }
    if (typeof data[firstKey] === "string") {
      return data[firstKey];
    }
    if (data.detail) {
      return data.detail;
    }
    return "Error al procesar la solicitud";
  };

  useEffect(() => {
    setPasswordErrors(password ? validatePassword(password) : []);
  }, [password]);

  // VALIDACIÓN EMAIL
  const validateEmail = (mail: string) => {
    const regex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return regex.test(mail);
  };

  const handleEmailChange = (value: string) => {
    setEmail(value);
    if (!validateEmail(value)) {
      setEmailError("Correo no válido");
    } else {
      setEmailError("");
    }
  };

  // CAMBIAR CONTRASEÑA
  const handlePasswordChange = async () => {
    if (!password || !confirmPassword) {
      toast.warning("Completa todos los campos.");
      return;
    }
    if (passwordErrors.length > 0) {
      toast.error("La contraseña no cumple los requisitos.");
      return;
    }
    if (password !== confirmPassword) {
      toast.error("Las contraseñas no coinciden.");
      return;
    }

    setIsSubmittingPassword(true);

    try {
      await api.patch(`/usuarios/usuarios/${userId}/`, {
        password: password,
      });

      toast.success("Contraseña actualizada correctamente");
      setPassword("");
      setConfirmPassword("");
    } catch (error: any) {
      console.error(error);
      toast.error(getErrorMessage(error));
    } finally {
      setIsSubmittingPassword(false);
    }
  };

  // CAMBIO DIRECTO DE EMAIL
  const handleEmailUpdate = async () => {
    if (!email) {
      toast.warning("Ingresa un correo.");
      return;
    }
    if (!validateEmail(email)) {
      toast.error("Correo inválido.");
      return;
    }

    setIsSubmittingEmail(true);

    try {
      await api.patch(`/usuarios/usuarios/${userId}/`, {
        email: email,
      });

      toast.success("Correo actualizado correctamente");
      setEmail("");
      
      // Refrescamos los datos para que la tarjeta de perfil muestre el nuevo correo
      fetchUserData(); 
    } catch (error: any) {
      console.error(error);
      toast.error(getErrorMessage(error));
    } finally {
      setIsSubmittingEmail(false);
    }
  };

  return (
    <div className="space-y-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-bold text-foreground dark:text-white">
        Ajustes
      </h1>

      {userRole === "admin" && (
        <Card className="p-4 sm:p-6 rounded-2xl shadow-md border border-border dark:bg-slate-900/80 backdrop-blur-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 pb-4 border-b border-border mb-6">
            <div className="flex items-center gap-2.5">
              <div className="p-2 rounded-xl bg-primary/10 text-primary">
                <ShieldCheck size={22} />
              </div>
              <div>
                <h3 className="font-bold text-lg text-foreground">Delegar permisos temporales</h3>
                <p className="text-xs text-muted-foreground">Genera un código de acceso para que un cajero acceda a módulos autorizados</p>
              </div>
            </div>
            <div className="flex items-center gap-1.5 self-start sm:self-auto">
              <span className="text-xs font-medium px-2.5 py-1 rounded-full bg-muted text-muted-foreground">
                {modulos.length} de {modulosDisponibles.length} seleccionados
              </span>
            </div>
          </div>

          {/* Selector de Cajero y Duración */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 mb-6">
            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Cajero Destino</Label>
              <select
                className="w-full h-11 rounded-xl border border-input bg-background/50 px-3 text-sm font-medium focus:ring-2 focus:ring-primary/20 focus:outline-none transition-all"
                value={empleadoId}
                onChange={(event) => setEmpleadoId(event.target.value)}
              >
                <option value="">Seleccionar cajero activo...</option>
                {empleados.map((empleado) => (
                  <option key={empleado.id} value={empleado.id}>
                    {empleado.nombre} ({empleado.email})
                  </option>
                ))}
              </select>
            </div>

            <div className="space-y-1.5">
              <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">Tiempo de Acceso</Label>
              <div className="relative">
                <select
                  className="w-full h-11 rounded-xl border border-input bg-background/50 px-3 pl-9 text-sm font-medium focus:ring-2 focus:ring-primary/20 focus:outline-none transition-all"
                  value={duracionHoras}
                  onChange={(event) => setDuracionHoras(event.target.value)}
                >
                  {[1, 2, 4, 8, 12, 24].map((hours) => (
                    <option key={hours} value={hours}>
                      {hours} {hours === 1 ? "hora" : "horas"} de sesión
                    </option>
                  ))}
                </select>
                <Clock className="size-4 text-muted-foreground absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
              </div>
            </div>

            <div className="flex sm:col-span-2 lg:col-span-1 items-end">
              <Button
                onClick={generarCodigoAdmin}
                className="w-full h-11 rounded-xl font-bold gap-2 shadow-sm"
              >
                <KeyRound size={17} />
                Generar código de acceso
              </Button>
            </div>
          </div>

          {/* Módulos Autorizados — Grid de Cards Interactivas */}
          <div className="space-y-3 mb-6">
            <div className="flex items-center justify-between">
              <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                Módulos que podrá utilizar
              </Label>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setModulos(modulosDisponibles.map((m) => m.key))}
                  className="text-xs text-primary hover:underline font-medium"
                >
                  Marcar todos
                </button>
                <span className="text-muted-foreground text-xs">•</span>
                <button
                  type="button"
                  onClick={() => setModulos([])}
                  className="text-xs text-muted-foreground hover:underline font-medium"
                >
                  Desmarcar
                </button>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-2.5">
              {modulosDisponibles.map((item) => {
                const IconComponent = item.icon;
                const isSelected = modulos.includes(item.key);
                return (
                  <button
                    key={item.key}
                    type="button"
                    onClick={() =>
                      setModulos((actuales) =>
                        isSelected
                          ? actuales.filter((m) => m !== item.key)
                          : [...actuales, item.key]
                      )
                    }
                    className={`group relative text-left p-3.5 rounded-xl border transition-all duration-200 flex items-start gap-3 select-none ${
                      isSelected
                        ? "border-primary bg-primary/10 shadow-sm ring-1 ring-primary/30 dark:bg-primary/15"
                        : "border-border bg-card/60 hover:bg-accent/50 hover:border-border/80"
                    }`}
                  >
                    <div
                      className={`p-2 rounded-lg shrink-0 transition-colors ${
                        isSelected
                          ? "bg-primary text-primary-foreground shadow-sm"
                          : "bg-muted text-muted-foreground group-hover:text-foreground"
                      }`}
                    >
                      <IconComponent size={18} />
                    </div>

                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-1">
                        <span className={`text-sm font-semibold truncate ${isSelected ? "text-primary dark:text-primary-foreground" : "text-foreground"}`}>
                          {item.label}
                        </span>
                        {isSelected && (
                          <div className="size-4 rounded-full bg-primary text-primary-foreground flex items-center justify-center shrink-0">
                            <Check size={10} strokeWidth={3} />
                          </div>
                        )}
                      </div>
                      <p className="text-[11px] text-muted-foreground line-clamp-1 mt-0.5">
                        {item.desc}
                      </p>
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {/* Código generado destacado */}
          {codigoGenerado && (
            <div className="p-4 rounded-xl bg-gradient-to-r from-emerald-500/10 via-emerald-500/5 to-transparent border border-emerald-500/30 mb-6 flex flex-col sm:flex-row sm:items-center justify-between gap-3 animate-in fade-in zoom-in-95">
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-emerald-500 text-white shadow-md">
                  <KeyRound size={20} />
                </div>
                <div>
                  <p className="text-xs font-semibold text-emerald-600 dark:text-emerald-400 uppercase tracking-wider">Código generado con éxito</p>
                  <p className="font-mono text-xl sm:text-2xl font-black tracking-widest text-foreground">{codigoGenerado}</p>
                </div>
              </div>
              <p className="text-xs text-muted-foreground sm:text-right max-w-xs">
                Compártelo con el cajero. Deberá ingresarlo en su pantalla de Ajustes para activar los permisos.
              </p>
            </div>
          )}

          {/* Historial de delegaciones emitidas */}
          {delegaciones.length > 0 && (
            <div className="space-y-2.5 border-t border-border pt-5">
              <div className="flex items-center justify-between">
                <Label className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  Delegaciones recientes
                </Label>
                <span className="text-xs text-muted-foreground">{delegaciones.length} registro(s)</span>
              </div>
              <div className="space-y-2 max-h-60 overflow-y-auto pr-1">
                {delegaciones.map((delegacion) => {
                  const estadoBadge = delegacion.revocado
                    ? { text: "Revocado", style: "bg-rose-500/10 text-rose-600 dark:text-rose-400 border-rose-500/20" }
                    : delegacion.concedidoHasta
                    ? { text: "Activo", style: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border-emerald-500/20" }
                    : { text: "Pendiente", style: "bg-amber-500/10 text-amber-600 dark:text-amber-400 border-amber-500/20" };

                  return (
                    <div
                      key={delegacion.id}
                      className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 rounded-xl border border-border bg-card/40 p-3 text-sm transition-colors hover:bg-muted/40"
                    >
                      <div className="flex flex-col gap-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <strong className="text-foreground font-semibold">{delegacion.empleado}</strong>
                          <span className={`text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded-full border ${estadoBadge.style}`}>
                            {estadoBadge.text}
                          </span>
                        </div>
                        <p className="text-xs text-muted-foreground truncate">
                          Módulos: <span className="text-foreground/80 font-medium">{delegacion.modulos.join(", ")}</span>
                        </p>
                      </div>

                      {!delegacion.revocado && (
                        <Button
                          variant="outline"
                          size="sm"
                          className="self-end sm:self-auto h-8 text-xs font-semibold hover:bg-destructive hover:text-destructive-foreground hover:border-destructive transition-colors shrink-0"
                          onClick={() => revocarDelegacion(delegacion.id)}
                        >
                          Revocar acceso
                        </Button>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </Card>
      )}

      {userRole !== "admin" && (
        <Card className="p-6 rounded-2xl shadow-md dark:bg-slate-900 dark:border dark:border-slate-800">
          <h3 className="font-bold text-lg mb-4 flex items-center gap-2">
            <KeyRound size={18} /> Activar permisos administrativos
          </h3>
          <div className="flex gap-3">
            <Input value={codigoAdmin} onChange={(event) => setCodigoAdmin(event.target.value.toUpperCase())} placeholder="Código del administrador" />
            <Button onClick={activarCodigoAdmin}>Activar</Button>
          </div>
        </Card>
      )}

      {userRole === "admin" && (
        <>
      {/* TARJETA DE DATOS DEL PERFIL */}
      <Card className="p-6 rounded-2xl shadow-md dark:bg-slate-900 dark:border dark:border-slate-800">
        <h3 className="font-bold text-lg mb-4 flex items-center gap-2">
          <User size={18} /> Datos del Perfil
        </h3>
        
        {isLoadingData ? (
          <div className="flex justify-center items-center p-6">
            <Loader2 className="animate-spin text-muted-foreground" />
          </div>
        ) : userData ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <Label className="text-muted-foreground dark:text-slate-400">Correo</Label>
              <p className="font-medium text-foreground dark:text-slate-100 mt-1">
                {userData.email || "No especificado"}
              </p>
            </div>

            <div>
              <Label className="text-muted-foreground dark:text-slate-400">Rol</Label>
              <p className="font-medium text-foreground dark:text-slate-100 mt-1 capitalize">
                {/* Ajusta este campo según como retorne el rol tu backend de Django (ej: userData.rol, userData.groups[0]?.name, etc) */}
                {userData.rol || userData.role || (userData.is_superuser ? "Administrador" : "Cajero") || "No especificado"}
              </p>
            </div>
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">No se encontró información del usuario.</p>
        )}
      </Card>

      <div className="grid md:grid-cols-2 gap-6">
        {/* PASSWORD */}
        <Card className="p-6 rounded-2xl shadow-md dark:bg-slate-900 dark:border dark:border-slate-800">
          <h3 className="font-bold text-lg mb-4">Cambio de contraseña</h3>

          <div className="space-y-4">
            <div>
              <Label>Nueva Contraseña</Label>
              <div className="relative">
                <Input
                  type={showPassword ? "text" : "password"}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground dark:text-slate-400 dark:hover:text-slate-200"
                >
                  {showPassword ? <EyeOff size={18} /> : <Eye size={18} />}
                </button>
              </div>

              {passwordErrors.length > 0 && (
                <ul className="text-red-500 text-sm mt-1">
                  {passwordErrors.map((e, i) => (
                    <li key={i}>• {e}</li>
                  ))}
                </ul>
              )}
            </div>

            <div>
              <Label>Confirmar Contraseña</Label>
              <Input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
              />

              {confirmPassword && password !== confirmPassword && (
                <p className="text-red-500 text-sm mt-1">
                  Las contraseñas no coinciden
                </p>
              )}
            </div>

            <Button
              onClick={handlePasswordChange}
              disabled={
                isSubmittingPassword ||
                !password ||
                !confirmPassword ||
                passwordErrors.length > 0 ||
                password !== confirmPassword
              }
              className="w-full"
            >
              {isSubmittingPassword ? (
                <Loader2 className="animate-spin mr-2" />
              ) : (
                <Save className="mr-2" />
              )}
              Actualizar contraseña
            </Button>
          </div>
        </Card>

        {/* EMAIL */}
        <Card className="p-6 rounded-2xl shadow-md dark:bg-slate-900 dark:border dark:border-slate-800">
          <h3 className="font-bold text-lg mb-4 flex items-center gap-2">
            <Mail size={18} /> Cambio de correo electrónico
          </h3>

          <div className="space-y-4">
            <div>
              <Label>Nuevo correo</Label>
              <Input
                value={email}
                onChange={(e) => handleEmailChange(e.target.value)}
                placeholder="correo@ejemplo.com"
                className={emailError ? "border-red-500" : ""}
              />

              {emailError && (
                <p className="text-red-500 text-sm mt-1">{emailError}</p>
              )}
            </div>

            <Button
              onClick={handleEmailUpdate}
              disabled={isSubmittingEmail || !email || !!emailError}
              className="w-full"
            >
              {isSubmittingEmail ? (
                <Loader2 className="animate-spin mr-2" />
              ) : (
                <Save className="mr-2" />
              )}
              Cambiar correo
            </Button>
          </div>
        </Card>
      </div>
        </>
      )}
    </div>
  );
}