import { Card } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { useState, useEffect } from "react";
import api from "../api/axiosInstance";
import { toast } from "sonner";
import { Loader2, Save, Eye, EyeOff, Mail, User, KeyRound, ShieldCheck } from "lucide-react";
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
    ["VENTAS", "Ventas"],
    ["PRODUCTOS", "Productos"],
    ["INVENTARIO", "Inventario y compras"],
    ["PERDIDAS", "Pérdidas"],
    ["DEVOLUCIONES", "Devoluciones"],
    ["REPORTES", "Reportes"],
    ["AUDITORIA", "Auditoría"],
    ["USUARIOS", "Usuarios"],
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
        setEmpleados(lista.filter((empleado: any) => ["cajero", "vendedor"].includes(empleado.rol)));
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
        <Card className="p-6 rounded-2xl shadow-md dark:bg-slate-900 dark:border dark:border-slate-800">
          <h3 className="font-bold text-lg mb-4 flex items-center gap-2">
            <ShieldCheck size={18} /> Delegar permisos administrativos
          </h3>
          <div className="grid md:grid-cols-3 gap-4 items-end">
            <div>
              <Label>Cajero</Label>
              <select className="w-full h-10 rounded-md border bg-background px-3" value={empleadoId} onChange={(event) => setEmpleadoId(event.target.value)}>
                <option value="">Seleccionar cajero</option>
                {empleados.map((empleado) => <option key={empleado.id} value={empleado.id}>{empleado.nombre}</option>)}
              </select>
            </div>
            <div>
              <Label>Duración</Label>
              <select className="w-full h-10 rounded-md border bg-background px-3" value={duracionHoras} onChange={(event) => setDuracionHoras(event.target.value)}>
                {[1, 2, 4, 8, 12, 24].map((hours) => <option key={hours} value={hours}>{hours} {hours === 1 ? "hora" : "horas"}</option>)}
              </select>
            </div>
            <div className="md:col-span-3">
              <Label>Módulos autorizados</Label>
              <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mt-2">
                {modulosDisponibles.map(([value, label]) => (
                  <label key={value} className="flex items-center gap-2 text-sm">
                    <input
                      type="checkbox"
                      checked={modulos.includes(value)}
                      onChange={(event) => setModulos((actuales) =>
                        event.target.checked
                          ? [...actuales, value]
                          : actuales.filter((modulo) => modulo !== value)
                      )}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
            <Button onClick={generarCodigoAdmin}><KeyRound className="mr-2" size={16} />Generar código</Button>
          </div>
          {codigoGenerado && <p className="mt-4 font-mono text-xl tracking-widest">Código: {codigoGenerado}</p>}
          {delegaciones.length > 0 && (
            <div className="mt-6 space-y-2">
              <Label>Delegaciones emitidas</Label>
              {delegaciones.map((delegacion) => (
                <div key={delegacion.id} className="flex flex-wrap items-center justify-between gap-2 rounded-md border p-3 text-sm">
                  <span>
                    <strong>{delegacion.empleado}</strong> · {delegacion.modulos.join(", ")}
                    {delegacion.revocado ? " · Revocado" : delegacion.concedidoHasta ? " · Activo" : " · Pendiente"}
                  </span>
                  {!delegacion.revocado && (
                    <Button variant="outline" size="sm" onClick={() => revocarDelegacion(delegacion.id)}>
                      Cortar acceso
                    </Button>
                  )}
                </div>
              ))}
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
    </div>
  );
}