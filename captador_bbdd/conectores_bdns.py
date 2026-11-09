"""
Módulo conector para la API de la Base de Datos Nacional de Subvenciones (BDNS).
Obtiene y estandariza las concesiones de CDTI y Red.es alineadas con el esquema
completo de datos (Sector, Tamaño/Tipo de empresa, Web, Área temática, Proyecto, etc.).
"""

import argparse
import csv
import io
import json
import logging
import re
import requests
import unicodedata
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import List, Dict, Any, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# Identificadores oficiales DIR3 de la Administración Pública Española
DIR3_CDTI = "EA0040332"
DIR3_RED_ES = "EA0042457"

BDNS_API_URL = "https://www.infosubvenciones.es/bdnstrans/GE/es/api/concesiones"
BDNS_API_BUSQUEDA_URL = (
    "https://www.infosubvenciones.es/bdnstrans/api/concesiones/busqueda"
)

CODIGO_BDNS_CDTI = "834244"
CODIGO_BDNS_RED_ES = "848058"
INFORMES_CSV_FIELDS = [
    "codigo_bdns",
    "id_concesion",
    "cif",
    "empresa",
    "organismo",
    "convocatoria",
    "fecha_concesion",
    "subvencion",
    "provincia",
    "comunidad_autonoma",
    "datos_originales_json",
]

_COLUMNAS_CSV = {
    "codigo_bdns": "codigo bdns",
    "id_concesion": "codigo de concesion",
    "organismo": "organo",
    "convocatoria": "convocatoria",
    "fecha_concesion": "fecha de concesion",
    "beneficiario": "beneficiario",
    "subvencion": "importe",
}
_COLUMNAS_CSV_ORIGINALES = (
    "administracion",
    "departamento",
    "organo",
    "codigo bdns",
    "convocatoria",
    "titulo cooficial",
    "bases reguladoras",
    "codigo de concesion",
    "fecha de concesion",
    "fecha de registro",
    "beneficiario",
    "importe",
    "instrumento",
    "ayuda equivalente",
)

_REGEX_ID_VISIBLE = re.compile(
    r"^(?:[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]|\d{8}[A-Z]|[XYZ]\d{7}[A-Z])$",
    re.IGNORECASE,
)
_REGEX_ID_ENMASCARADO = re.compile(r"^\*{2,}\d{2,}\*{2,}$")


def _normalizar_nombre_columna(nombre: str) -> str:
    """Normaliza cabeceras para aceptar CSV UTF-8 y Windows-1252."""
    sin_tildes = unicodedata.normalize("NFKD", nombre.strip().lstrip("\ufeff"))
    return "".join(
        caracter
        for caracter in sin_tildes
        if not unicodedata.combining(caracter)
    ).casefold()


def _leer_csv_bdns(ruta: Path) -> Tuple[List[Dict[str, str]], List[Dict[str, Any]]]:
    """Lee un CSV oficial y valida las columnas necesarias para la extracción."""
    try:
        contenido = ruta.read_bytes()
    except OSError as error:
        raise RuntimeError(f"No se pudo leer el CSV de BDNS {ruta}: {error}") from error

    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        texto = contenido.decode("cp1252")

    lector = csv.DictReader(io.StringIO(texto))
    if not lector.fieldnames:
        raise RuntimeError(f"El CSV de BDNS {ruta} no contiene cabeceras.")

    cabeceras = {
        _normalizar_nombre_columna(nombre): nombre
        for nombre in lector.fieldnames
        if nombre is not None
    }
    columnas = {
        campo: cabeceras.get(_normalizar_nombre_columna(nombre))
        for campo, nombre in _COLUMNAS_CSV.items()
    }
    columnas_originales_normalizadas = {
        _normalizar_nombre_columna(nombre)
        for nombre in _COLUMNAS_CSV_ORIGINALES
    }
    faltantes = [
        _COLUMNAS_CSV[campo]
        for campo, nombre_original in columnas.items()
        if nombre_original is None
    ]
    if faltantes:
        raise RuntimeError(
            f"El CSV de BDNS {ruta} no incluye las columnas requeridas: "
            + ", ".join(faltantes)
        )

    filas: List[Dict[str, str]] = []
    errores: List[Dict[str, Any]] = []
    for numero_fila, fila in enumerate(lector, start=2):
        if not fila or not any(valor for valor in fila.values() if valor):
            continue
        if None in fila:
            errores.append({
                "archivo": str(ruta),
                "fila": numero_fila,
                "tipo": "fila_con_columnas_extra",
                "detalle": "La fila contiene más valores que la cabecera.",
            })
            continue
        if any(valor is None for valor in fila.values()):
            errores.append({
                "archivo": str(ruta),
                "fila": numero_fila,
                "tipo": "fila_incompleta",
                "detalle": "La fila contiene menos valores que la cabecera.",
            })
            continue

        valores = {
            campo: (fila[nombre_original] or "").strip()
            for campo, nombre_original in columnas.items()
        }
        valores["datos_originales"] = {
            nombre: (fila[nombre] or "").strip()
            for nombre in lector.fieldnames
            if nombre is not None
            and _normalizar_nombre_columna(nombre)
            in columnas_originales_normalizadas
        }
        filas.append(valores)
    return filas, errores


def _separar_beneficiario(beneficiario: str) -> Tuple[Optional[str], str, Optional[str]]:
    """Separa un CIF/NIF visible o enmascarado del nombre del beneficiario."""
    texto = beneficiario.strip()
    if not texto:
        return None, "", None

    coincidencia = re.match(
        r"^(?:(?:CIF|NIF)\s*[:\-]?\s*)?(\S+)(?:\s+(.*))?$",
        texto,
        re.IGNORECASE,
    )
    if not coincidencia:
        return None, texto, None

    identificador = coincidencia.group(1).strip(".,;")
    nombre = (coincidencia.group(2) or "").strip()
    if _REGEX_ID_VISIBLE.fullmatch(identificador):
        return identificador.upper(), nombre, identificador
    if _REGEX_ID_ENMASCARADO.fullmatch(identificador):
        return None, nombre, identificador
    return None, texto, None


def _normalizar_fecha_bdns(fecha: str) -> Optional[str]:
    """Convierte una fecha de exportación BDNS a formato ISO."""
    valor = fecha.strip()
    if not valor:
        return None
    for formato in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(valor, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _normalizar_importe_bdns(importe: str) -> Optional[str]:
    """Valida el importe y devuelve una representación decimal estable."""
    valor = importe.strip().replace(" ", "")
    if not valor:
        return None
    if "," in valor and "." not in valor:
        valor = valor.replace(",", ".")
    try:
        return format(Decimal(valor), "f")
    except InvalidOperation:
        return None


def _normalizar_fila_exportacion(
    fila: Dict[str, str],
    codigo_esperado: str,
    organismo: str,
    ruta_origen: Path,
    numero_fila: int,
) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """Mapea una fila oficial a la estructura de extracción de FASE 1."""
    errores: List[Dict[str, Any]] = []
    if fila["codigo_bdns"] != codigo_esperado:
        return None, errores

    cif, empresa, identificador_beneficiario = _separar_beneficiario(
        fila["beneficiario"]
    )
    if not empresa:
        errores.append({
            "archivo": str(ruta_origen),
            "fila": numero_fila,
            "tipo": "beneficiario_vacio",
            "detalle": "No se pudo identificar el beneficiario.",
        })

    fecha = _normalizar_fecha_bdns(fila["fecha_concesion"])
    if fecha is None:
        errores.append({
            "archivo": str(ruta_origen),
            "fila": numero_fila,
            "tipo": "fecha_invalida",
            "detalle": f"Fecha de concesión no reconocida: {fila['fecha_concesion']!r}.",
        })

    importe = _normalizar_importe_bdns(fila["subvencion"])
    if importe is None:
        errores.append({
            "archivo": str(ruta_origen),
            "fila": numero_fila,
            "tipo": "importe_invalido",
            "detalle": f"Importe no reconocido: {fila['subvencion']!r}.",
        })

    id_concesion = fila["id_concesion"]
    if not id_concesion:
        errores.append({
            "archivo": str(ruta_origen),
            "fila": numero_fila,
            "tipo": "id_concesion_vacio",
            "detalle": "No se puede deduplicar la fila sin código de concesión.",
        })

    return {
        "codigo_bdns": fila["codigo_bdns"],
        "id_concesion": id_concesion,
        "cif": cif,
        "empresa": empresa or None,
        "organismo": organismo,
        "convocatoria": fila["convocatoria"],
        "fecha_concesion": fecha,
        "subvencion": importe,
        "provincia": None,
        "comunidad_autonoma": None,
        "datos_originales_json": json.dumps(
            fila.get("datos_originales", {}),
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }, errores


def _normalizar_item_api(
    item: Dict[str, Any],
    codigo_bdns: str,
    organismo: str,
    numero_fila: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Adapta un registro documentado de la API al esquema de exportación."""
    codigo_recibido = str(item.get("numeroConvocatoria") or "").strip()
    if codigo_recibido != codigo_bdns:
        raise RuntimeError(
            f"La API devolvió numeroConvocatoria={codigo_recibido!r} "
            f"al consultar {codigo_bdns} (registro {numero_fila})."
        )

    fila = {
        "codigo_bdns": codigo_recibido,
        "id_concesion": str(item.get("codConcesion") or "").strip(),
        "organismo": organismo,
        "convocatoria": str(item.get("convocatoria") or "").strip(),
        "fecha_concesion": str(item.get("fechaConcesion") or "").strip(),
        "beneficiario": str(item.get("beneficiario") or "").strip(),
        "subvencion": str(item.get("importe") if item.get("importe") is not None else ""),
        "datos_originales": item,
    }
    return _normalizar_fila_exportacion(
        fila,
        codigo_bdns,
        organismo,
        Path(f"api_{codigo_bdns}.json"),
        numero_fila,
    )


def _solicitar_pagina_api(
    codigo_bdns: str,
    page: int,
    page_size: int,
) -> Dict[str, Any]:
    """Solicita una página usando únicamente el contrato público documentado."""
    try:
        respuesta = requests.get(
            BDNS_API_BUSQUEDA_URL,
            params={
                "vpd": "GE",
                "numeroConvocatoria": codigo_bdns,
                "page": page,
                "pageSize": page_size,
            },
            headers={"Accept": "application/json"},
            timeout=30,
        )
        respuesta.raise_for_status()
        datos = respuesta.json()
    except (requests.RequestException, ValueError) as error:
        log.exception(
            "Error consultando BDNS para convocatoria %s, página %s",
            codigo_bdns,
            page,
        )
        raise RuntimeError(
            f"Error consultando BDNS para convocatoria {codigo_bdns}, "
            f"página {page}"
        ) from error

    if not isinstance(datos, dict):
        raise RuntimeError(
            f"Respuesta BDNS inesperada para convocatoria {codigo_bdns}, "
            f"página {page}: se esperaba un objeto JSON."
        )
    return datos


def obtener_concesiones_convocatoria_api(
    codigo_bdns: str,
    organismo: str,
    page_size: int = 50,
) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """Obtiene todas las páginas de una convocatoria usando totalPages."""
    if page_size <= 0:
        raise ValueError("page_size debe ser mayor que cero.")

    primera_pagina = _solicitar_pagina_api(codigo_bdns, 0, page_size)
    total_paginas = primera_pagina.get("totalPages")
    total_elementos = primera_pagina.get("totalElements")
    if (
        not isinstance(total_paginas, int)
        or total_paginas < 0
        or not isinstance(total_elementos, int)
        or total_elementos < 0
    ):
        raise RuntimeError(
            f"Metadatos de paginación inválidos para convocatoria {codigo_bdns}."
        )

    paginas = [primera_pagina]
    for numero_pagina in range(1, total_paginas):
        paginas.append(
            _solicitar_pagina_api(codigo_bdns, numero_pagina, page_size)
        )

    concesiones: List[Dict[str, Any]] = []
    errores: List[Dict[str, Any]] = []
    for numero_pagina, datos in enumerate(paginas):
        if (
            datos.get("totalPages") != total_paginas
            or datos.get("totalElements") != total_elementos
            or datos.get("number") != numero_pagina
        ):
            raise RuntimeError(
                f"Metadatos de paginación inconsistentes para convocatoria "
                f"{codigo_bdns}, página {numero_pagina}."
            )
        items = datos.get("content")
        if not isinstance(items, list):
            raise RuntimeError(
                f"Respuesta BDNS inválida para convocatoria {codigo_bdns}, "
                f"página {numero_pagina}: 'content' no es una lista."
            )
        if datos.get("numberOfElements") != len(items):
            raise RuntimeError(
                f"El número de registros no coincide con numberOfElements "
                f"para convocatoria {codigo_bdns}, página {numero_pagina}."
            )
        if numero_pagina < total_paginas - 1 and len(items) != page_size:
            raise RuntimeError(
                f"Página incompleta inesperada para convocatoria {codigo_bdns}, "
                f"página {numero_pagina}."
            )

        for indice, item in enumerate(items):
            if not isinstance(item, dict):
                raise RuntimeError(
                    f"Registro inválido en convocatoria {codigo_bdns}, "
                    f"página {numero_pagina}, posición {indice}."
                )
            concesion, errores_fila = _normalizar_item_api(
                item,
                codigo_bdns,
                organismo,
                numero_pagina * page_size + indice + 1,
            )
            concesiones.append(concesion)
            for error in errores_fila:
                error["codigo_bdns"] = codigo_bdns
                error["pagina"] = numero_pagina
                errores.append(error)

    if len(concesiones) != total_elementos:
        raise RuntimeError(
            f"Extracción incompleta para convocatoria {codigo_bdns}: "
            f"se esperaban {total_elementos} registros y se obtuvieron "
            f"{len(concesiones)}."
        )
    return concesiones, total_paginas, errores


def _deduplicar_concesiones(
    concesiones: Sequence[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], int, List[Dict[str, Any]]]:
    """Deduplica exclusivamente por código de concesión."""
    resultado: List[Dict[str, Any]] = []
    vistos: Dict[str, Dict[str, Any]] = {}
    duplicados = 0
    errores: List[Dict[str, Any]] = []

    for concesion in concesiones:
        identificador = str(concesion.get("id_concesion") or "").strip()
        if not identificador:
            resultado.append(concesion)
            continue
        if identificador not in vistos:
            vistos[identificador] = concesion
            resultado.append(concesion)
            continue

        duplicados += 1
        original = vistos[identificador]
        if any(original.get(campo) != concesion.get(campo) for campo in (
            "codigo_bdns", "cif", "empresa", "fecha_concesion", "subvencion"
        )):
            errores.append({
                "codigo_bdns": concesion["codigo_bdns"],
                "id_concesion": identificador,
                "tipo": "id_concesion_con_datos_conflictivos",
                "detalle": (
                    f"El identificador {identificador} aparece con datos "
                    "distintos; se conserva la primera fila."
                ),
            })
    return resultado, duplicados, errores


def _escribir_csv(ruta: Path, concesiones: Sequence[Dict[str, Any]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    with ruta.open("w", newline="", encoding="utf-8-sig") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=INFORMES_CSV_FIELDS)
        escritor.writeheader()
        escritor.writerows(concesiones)


def _resumen_convocatoria(
    filas_fuente: int,
    concesiones: Sequence[Dict[str, Any]],
    duplicados: int,
    filas_fuera_de_codigo: int,
    errores: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    beneficiarios = {
        (
            f"cif:{concesion['cif'].casefold()}"
            if concesion.get("cif")
            else f"empresa:{' '.join(concesion['empresa'].split()).casefold()}"
        )
        for concesion in concesiones
        if concesion.get("empresa")
    }
    registros_con_cif = sum(bool(concesion.get("cif")) for concesion in concesiones)
    return {
        "filas_de_convocatoria_en_origen": filas_fuente,
        "concesiones_recuperadas": len(concesiones),
        "beneficiarios_identificados": len(beneficiarios),
        "registros_con_cif": registros_con_cif,
        "registros_sin_cif": len(concesiones) - registros_con_cif,
        "duplicados_detectados": duplicados,
        "filas_ignoradas_por_otro_codigo_bdns": filas_fuera_de_codigo,
        "posibles_errores_extraccion": len(errores),
    }


def procesar_exportaciones_csv(
    csv_cdti: Path,
    csv_red_es: Path,
    directorio_salida: Path = Path("data/bdns_extraccion"),
) -> Dict[str, Any]:
    """
    Procesa exportaciones CSV oficiales sin filtros territoriales ni sincronización.

    Es una ruta independiente de ``obtener_todas_las_concesiones`` para preservar
    la compatibilidad del pipeline actual de ``main.py``.
    """
    configuraciones = (
        (Path(csv_cdti), CODIGO_BDNS_CDTI, "CDTI", "cdti_834244"),
        (Path(csv_red_es), CODIGO_BDNS_RED_ES, "Red.es", "red_es_848058"),
    )
    directorio_salida = Path(directorio_salida)
    resumenes: Dict[str, Any] = {}
    errores_totales: List[Dict[str, Any]] = []
    registros_por_convocatoria: List[List[Dict[str, Any]]] = []
    duplicados_totales = 0

    for ruta, codigo, organismo, sufijo in configuraciones:
        filas, errores_lectura = _leer_csv_bdns(ruta)
        errores_totales.extend(errores_lectura)
        normalizadas: List[Dict[str, Any]] = []
        fuera_de_codigo = 0
        for numero_fila, fila in enumerate(filas, start=2):
            if fila["codigo_bdns"] != codigo:
                fuera_de_codigo += 1
                continue
            concesion, errores_fila = _normalizar_fila_exportacion(
                fila, codigo, organismo, ruta, numero_fila
            )
            errores_totales.extend(errores_fila)
            if concesion is not None:
                normalizadas.append(concesion)

        unicas, duplicados, errores_duplicados = _deduplicar_concesiones(normalizadas)
        duplicados_totales += duplicados
        errores_totales.extend(errores_duplicados)
        registros_por_convocatoria.append(unicas)

        errores_convocatoria = [
            error for error in errores_totales
            if error.get("archivo") == str(ruta)
        ]
        resumenes[codigo] = {
            "organismo": organismo,
            **_resumen_convocatoria(
                len(normalizadas),
                unicas,
                duplicados,
                fuera_de_codigo,
                errores_convocatoria,
            ),
        }
        _escribir_csv(
            directorio_salida / f"concesiones_{sufijo}.csv",
            unicas,
        )

    combinadas = [
        concesion
        for concesiones in registros_por_convocatoria
        for concesion in concesiones
    ]
    consolidadas, duplicados_consolidados, errores_consolidados = (
        _deduplicar_concesiones(combinadas)
    )
    errores_totales.extend(errores_consolidados)
    _escribir_csv(directorio_salida / "concesiones_consolidado.csv", consolidadas)

    informe = {
        "convocatorias": resumenes,
        "total_concesiones_consolidadas": len(consolidadas),
        "duplicados_detectados_en_consolidado": duplicados_consolidados,
        "duplicados_detectados_total": duplicados_totales + duplicados_consolidados,
        "posibles_errores_extraccion": errores_totales,
        "archivos_generados": [
            "concesiones_cdti_834244.csv",
            "concesiones_red_es_848058.csv",
            "concesiones_consolidado.csv",
            "informe_extraccion.json",
        ],
    }
    with (directorio_salida / "informe_extraccion.json").open(
        "w", encoding="utf-8"
    ) as archivo:
        json.dump(informe, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")
    return informe


def procesar_convocatorias_api(
    directorio_salida: Path = Path("data/bdns_extraccion"),
    page_size: int = 50,
) -> Dict[str, Any]:
    """Descarga, normaliza y exporta las dos convocatorias mediante la API."""
    configuraciones = (
        (CODIGO_BDNS_CDTI, "CDTI", "cdti_834244"),
        (CODIGO_BDNS_RED_ES, "Red.es", "red_es_848058"),
    )
    directorio_salida = Path(directorio_salida)
    resumenes: Dict[str, Any] = {}
    registros_por_convocatoria: List[List[Dict[str, Any]]] = []
    errores_totales: List[Dict[str, Any]] = []
    duplicados_totales = 0

    for codigo, organismo, sufijo in configuraciones:
        normalizadas, total_paginas, errores_api = (
            obtener_concesiones_convocatoria_api(codigo, organismo, page_size)
        )
        unicas, duplicados, errores_duplicados = _deduplicar_concesiones(normalizadas)
        errores_convocatoria = errores_api + errores_duplicados
        errores_totales.extend(errores_convocatoria)
        duplicados_totales += duplicados
        registros_por_convocatoria.append(unicas)

        resumenes[codigo] = {
            "organismo": organismo,
            "paginas_recuperadas": total_paginas,
            "filas_de_convocatoria_en_origen": len(normalizadas),
            **_resumen_convocatoria(
                len(normalizadas),
                unicas,
                duplicados,
                0,
                errores_convocatoria,
            ),
        }
        _escribir_csv(
            directorio_salida / f"concesiones_{sufijo}.csv",
            unicas,
        )

    combinadas = [
        concesion
        for concesiones in registros_por_convocatoria
        for concesion in concesiones
    ]
    consolidadas, duplicados_consolidados, errores_consolidados = (
        _deduplicar_concesiones(combinadas)
    )
    errores_totales.extend(errores_consolidados)
    _escribir_csv(directorio_salida / "concesiones_consolidado.csv", consolidadas)

    informe = {
        "fuente": "API oficial BDNS",
        "endpoint": BDNS_API_BUSQUEDA_URL,
        "parametros_base": {"vpd": "GE", "pageSize": page_size},
        "convocatorias": resumenes,
        "total_concesiones_consolidadas": len(consolidadas),
        "duplicados_detectados_en_consolidado": duplicados_consolidados,
        "duplicados_detectados_total": duplicados_totales + duplicados_consolidados,
        "posibles_errores_extraccion": errores_totales,
        "archivos_generados": [
            "concesiones_cdti_834244.csv",
            "concesiones_red_es_848058.csv",
            "concesiones_consolidado.csv",
            "informe_extraccion.json",
        ],
    }
    with (directorio_salida / "informe_extraccion.json").open(
        "w", encoding="utf-8"
    ) as archivo:
        json.dump(informe, archivo, ensure_ascii=False, indent=2)
        archivo.write("\n")
    return informe


def _main_extraccion_csv() -> None:
    parser = argparse.ArgumentParser(
        description="Extrae y exporta concesiones de la BDNS."
    )
    parser.add_argument(
        "--fuente",
        choices=("api", "csv"),
        default="api",
        help="Fuente de extracción (por defecto: API oficial).",
    )
    parser.add_argument("--cdti-csv", type=Path)
    parser.add_argument("--redes-csv", type=Path)
    parser.add_argument(
        "--salida",
        type=Path,
        default=Path("data/bdns_extraccion"),
        help="Directorio de salida (por defecto: data/bdns_extraccion).",
    )
    parser.add_argument("--page-size", type=int, default=50)
    argumentos = parser.parse_args()
    if argumentos.fuente == "api":
        informe = procesar_convocatorias_api(
            argumentos.salida,
            argumentos.page_size,
        )
    else:
        if argumentos.cdti_csv is None or argumentos.redes_csv is None:
            parser.error("--cdti-csv y --redes-csv son obligatorios con --fuente csv.")
        informe = procesar_exportaciones_csv(
            argumentos.cdti_csv,
            argumentos.redes_csv,
            argumentos.salida,
        )
    print(json.dumps(informe, ensure_ascii=False, indent=2))


def _normalizar_item_bdns(item: Dict[str, Any], organismo_default: str) -> Dict[str, Any]:
    """
    Estructura la respuesta de la BDNS (o dataset local) alineándose con la 
    estructura del Excel de referencia, eliminando el CP e incorporando 
    sector, tamaño de empresa, web, área temática y proyecto.
    """
    # Identificador y expediente
    id_concesion = str(
        item.get("idConcesion") or 
        item.get("id") or 
        item.get("numExpediente") or 
        item.get("Expediente") or 
        ""
    ).strip()

    # Importes económicos
    presupuesto = float(
        item.get("presupuestoAceptado") or 
        item.get("presupuesto") or 
        item.get("Presupuesto (€)") or 
        0.0
    )
    subvencion = float(
        item.get("importeSubvencion") or 
        item.get("importe") or 
        item.get("Subvención (€)") or 
        0.0
    )

    # Web de la empresa
    web_raw = str(item.get("web") or item.get("paginaWeb") or item.get("url") or "").strip()
    if web_raw and not web_raw.startswith("http") and len(web_raw) > 3 and web_raw != "nan":
        web_raw = f"https://{web_raw}"

    return {
        # --- Datos de la Empresa ---
        "empresa": str(item.get("beneficiarioRazonSocial") or item.get("beneficiario") or item.get("Empresa") or "Empresa Desconocida").strip(),
        "cif": str(item.get("beneficiarioNif") or item.get("nif") or item.get("CIF") or "").strip(),
        "tipo_empresa": str(item.get("tipoEmpresa") or item.get("tamanoEmpresa") or item.get("Tipo de empresa") or "Sin especificar").strip(),
        "sector": str(item.get("actividadEconomica") or item.get("sector") or item.get("Sector") or "Sin especificar").strip(),
        "subsector_cnae": str(item.get("subsectorCnae") or item.get("cnae") or item.get("Subsector CNAE") or "").strip(),
        "numero_trabajadores": str(item.get("numeroTrabajadores") or item.get("numTrabajadores") or item.get("Número de trabajadores") or "").strip(),
        "web": web_raw if web_raw != "nan" else "",

        # --- Datos Geográficos y Delegación ---
        "comunidad_autonoma": str(item.get("comunidadAutonoma") or item.get("ccaa") or item.get("CCAA") or "").strip(),
        "provincia": str(item.get("provincia") or item.get("Provincia") or "").strip(),
        "ciudad": str(item.get("municipio") or item.get("localidad") or item.get("Ciudad") or "").strip(),
        "codigo_postal": str(item.get("codigoPostal") or item.get("cp") or item.get("Código Postal") or "").strip(),
        "delegaciones": str(item.get("delegaciones") or item.get("Delegaciones") or "").strip(),
        "presencia_andalucia": str(item.get("presenciaAndalucia") or item.get("Presencia en Andalucía") or "").strip(),

        # --- Datos de la Subvención y Proyecto ---
        "organismo": str(item.get("organismo") or item.get("Organo concedente") or organismo_default).strip(),
        "convocatoria": str(item.get("tituloConvocatoria") or item.get("convocatoria") or item.get("Programa de la Subvención") or "").strip(),
        "proyecto": str(item.get("tituloProyecto") or item.get("descripcion") or item.get("Proyecto") or "Sin título de proyecto").strip(),
        "expediente": str(item.get("numExpediente") or item.get("expediente") or item.get("Expediente") or "").strip(),
        "area_tematica": str(item.get("areaTematica") or item.get("lineaAccion") or item.get("Área temática") or "").strip(),
        "duracion": str(item.get("duracion") or item.get("Duración") or "").strip(),
        "mercado_eurecat": str(item.get("mercadoEurecat") or item.get("Mercado Eurecat") or "").strip(),

        # --- Fechas y Económicos ---
        "fecha_concesion": str(item.get("fechaConcesion") or item.get("fecha") or item.get("Año convocatoria") or "")[:10],
        "presupuesto": presupuesto,
        "subvencion": subvencion,
        "estado_concesion": str(item.get("estado") or item.get("Estado") or "Estimada").strip(),
        "motivo_desestimacion": str(item.get("motivoDesestimacion") or item.get("Motivo de la desestimación") or "").strip(),
        "id_concesion": id_concesion
    }


def obtener_concesiones_organismo(
    codigo_dir3: str, 
    organismo_nombre: str, 
    fecha_desde: str = "2024-01-01", 
    fecha_hasta: str = "2026-12-31"
) -> List[Dict[str, Any]]:
    """Consulta paginada a la API de la BDNS por código DIR3 y rango de fechas."""
    concesiones = []
    page = 0
    page_size = 100

    headers = {
        "User-Agent": "GrantHunterAI/1.0",
        "Accept": "application/json"
    }

    while True:
        params = {
            "dir3": codigo_dir3,
            "fechaDesde": fecha_desde,
            "fechaHasta": fecha_hasta,
            "page": page,
            "pageSize": page_size
        }

        try:
            r = requests.get(BDNS_API_URL, params=params, headers=headers, timeout=20)
            r.raise_for_status()
            datos = r.json()
        except (requests.RequestException, ValueError) as e:
            log.exception("Fallo consultando BDNS para %s (página %s)", organismo_nombre, page)
            raise RuntimeError(
                f"Fallo consultando BDNS para {organismo_nombre} (página {page})"
            ) from e

        items = datos.get("items", datos) if isinstance(datos, dict) else datos
        if not isinstance(items, list):
            raise RuntimeError(
                f"Respuesta BDNS inesperada para {organismo_nombre} (página {page})"
            )
        if not items:
            break

        for item in items:
            concesion_norm = _normalizar_item_bdns(item, organismo_nombre)
            concesiones.append(concesion_norm)

        if len(items) < page_size:
            break

        page += 1

    return concesiones


def obtener_todas_las_concesiones() -> List[Dict[str, Any]]:
    """Extrae y unifica las concesiones de CDTI y Red.es."""
    print("Iniciando consulta a la API BDNS para CDTI y Red.es...")
    
    concesiones_cdti = obtener_concesiones_organismo(DIR3_CDTI, "CDTI")
    print(f" -> CDTI: {len(concesiones_cdti)} concesiones obtenidas.")

    concesiones_redes = obtener_concesiones_organismo(DIR3_RED_ES, "Red.es")
    print(f" -> Red.es: {len(concesiones_redes)} concesiones obtenidas.")

    total = concesiones_cdti + concesiones_redes
    print(f"Total concesiones capturadas BDNS: {len(total)}")
    return total


if __name__ == "__main__":
    _main_extraccion_csv()