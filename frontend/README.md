
# SistemaVentas

Este directorio contiene la aplicación React/TypeScript de SistemaVentas.

## Instalación y desarrollo

Desde la raíz del repositorio:

```powershell
npm --prefix frontend install
npm --prefix frontend run dev
```

## Lectura de códigos con cámara

El lector admite EAN-13, EAN-8, UPC-A y Code 128. Usa `BarcodeDetector`
solamente cuando el navegador declara los cuatro formatos requeridos; si la API
no existe o le falta alguno, carga de forma diferida el lector local ZXing. No se
descargan scripts desde un CDN ni se transmiten imágenes.

El acceso a la cámara requiere un contexto seguro. `localhost` se considera
seguro para desarrollo en el mismo equipo, pero abrir desde un celular una URL
HTTP con la IP local no cumple esa excepción: para esa prueba se necesita HTTPS
y aceptar el certificado correspondiente. El soporte final también depende del
navegador, del dispositivo y de sus permisos; la entrada manual y los lectores
que actúan como teclado permanecen disponibles.

### Equivalencia UPC-A/EAN-13

La búsqueda considera equivalentes únicamente un UPC-A numérico de 12 dígitos
y un EAN-13 numérico de 13 dígitos que empieza por cero, siempre que el dígito
de control sea válido. La conversión añade o retira exactamente ese primer cero;
el código almacenado no se modifica. EAN-8, valores alfanuméricos, checksums
inválidos y otras longitudes continúan usando coincidencia exacta.

Por esta regla, un código numérico interno que tenga una de esas formas y un
checksum válido comparte el mismo espacio de equivalencia. La validación de la
aplicación rechaza variantes ya ocupadas dentro de la misma miscelánea, pero la
restricción de base de datos sigue garantizando solo unicidad textual: dos
variantes podrían entrar en una carrera concurrente. Si ambas existen, la
búsqueda responde 409 y no elige un producto arbitrariamente; deben corregirse
manualmente los códigos del catálogo.

Consulta [ATTRIBUTIONS.md](./ATTRIBUTIONS.md) para las licencias de terceros.
