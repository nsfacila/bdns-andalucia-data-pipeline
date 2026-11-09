"""Enriquecimiento separado de las concesiones BDNS mediante APIEmpresas.es."""

from __future__ import annotations

import csv
import logging
import os
import re
import time
import unicodedata
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

import requests

log = logging.getLogger(__name__)

API_BASE_URL = "https://apiempresas.es/api/v1"
API_TIMEOUT = 30
INTERVALO_CONSULTA_SEGUNDOS = 0.6
CACHE_PATH = Path("data") / "enriquecimiento_territorial" / "empresas.csv"
CONCESIONES_ENRIQUECIDAS_PATH = (
    Path("data") / "enriquecimiento_territorial" / "concesiones_enriquecidas.csv"
)

CAMPOS_ENRIQUECIMIENTO = [
    "cif",
    "empresa_registral",
    "domicilio_social",
    "codigo_postal",
    "municipio",
    "provincia",
    "comunidad_autonoma",
    "clasificacion_territorial",
    "presencia_andalucia",
    "nivel_confianza",
    "estado_verificacion",
    "fuente_verificacion",
    "referencia_verificacion",
    "fecha_verificacion",
    "fecha_dato_fuente",
    "motivo_enriquecimiento",
]
_CAMPOS_CONCESIONES_ENRIQUECIDAS = [
    "codigo_bdns",
    "id_concesion",
    "cif",
    "empresa",
    "empresa_registral",
    "organismo",
    "convocatoria",
    "fecha_concesion",
    "subvencion",
    "domicilio_social",
    "codigo_postal",
    "municipio",
    "provincia",
    "comunidad_autonoma",
    "clasificacion_territorial",
    "presencia_andalucia",
    "nivel_confianza",
    "estado_verificacion",
    "fuente_verificacion",
    "referencia_verificacion",
    "fecha_verificacion",
    "fecha_dato_fuente",
    "motivo_enriquecimiento",
    "datos_originales_json",
]

_CIF = re.compile(r"^[ABCDEFGHJNPQRSUVW]\d{7}[0-9A-J]$", re.IGNORECASE)
_LETRAS_CONTROL = "JABCDEFGHI"


class EnriquecimientoConfigurationError(RuntimeError):
    """Error de configuración que impide consultar la API de enriquecimiento."""


class EnriquecimientoAPIError(RuntimeError):
    """Error de comunicación o formato de la API de enriquecimiento."""


class ConsultaNoDisponibleError(EnriquecimientoAPIError):
    """La API rechaza una consulta por cuota o límite temporal."""


def normalizar_cif(valor: Any) -> str:
    """Normaliza un CIF/NIF de sociedad sin deducir ubicación."""
    if valor is None:
        return ""
    return re.sub(r"[\s./-]", "", str(valor)).upper()


def validar_cif(valor: Any) -> bool:
    """Valida formato y dígito de control del CIF español de persona jurídica."""
    cif = normalizar_cif(valor)
    if not _CIF.fullmatch(cif):
        return False

    suma = 0
    for posicion, caracter in enumerate(cif[1:8], start=1):
        digito = int(caracter)
        if posicion % 2:
            doble = digito * 2
            suma += doble // 10 + doble % 10
        else:
            suma += digito

    control = (10 - suma % 10) % 10
    control_numerico = str(control)
    control_alfabetico = _LETRAS_CONTROL[control]
    tipo = cif[0]
    if tipo in "ABEH":
        controles_validos = {control_numerico}
    elif tipo in "PQRSNW":
        controles_validos = {control_alfabetico}
    else:
        controles_validos = {control_numerico, control_alfabetico}
    return cif[-1] in controles_validos


def normalizar_texto(valor: Any) -> str:
    """Normaliza nombres territoriales para búsquedas exactas en catálogos."""
    if not valor:
        return ""
    texto = unicodedata.normalize("NFKD", str(valor).strip().casefold())
    return "".join(
        caracter
        for caracter in texto
        if not unicodedata.combining(caracter)
    )


_PROVINCIAS_CCAA = {
    "alava": ("Álava", "País Vasco"),
    "araba": ("Álava", "País Vasco"),
    "albacete": ("Albacete", "Castilla-La Mancha"),
    "alicante": ("Alicante", "Comunidad Valenciana"),
    "alacant": ("Alicante", "Comunidad Valenciana"),
    "illes balears": ("Illes Balears", "Islas Baleares"),
    "islas baleares": ("Illes Balears", "Islas Baleares"),
    "baleares": ("Illes Balears", "Islas Baleares"),
    "almeria": ("Almería", "Andalucía"),
    "asturias": ("Asturias", "Principado de Asturias"),
    "avila": ("Ávila", "Castilla y León"),
    "badajoz": ("Badajoz", "Extremadura"),
    "barcelona": ("Barcelona", "Cataluña"),
    "burgos": ("Burgos", "Castilla y León"),
    "caceres": ("Cáceres", "Extremadura"),
    "cadiz": ("Cádiz", "Andalucía"),
    "cantabria": ("Cantabria", "Cantabria"),
    "castellon": ("Castellón", "Comunidad Valenciana"),
    "castello": ("Castellón", "Comunidad Valenciana"),
    "ciudad real": ("Ciudad Real", "Castilla-La Mancha"),
    "cordoba": ("Córdoba", "Andalucía"),
    "a coruna": ("A Coruña", "Galicia"),
    "la coruna": ("A Coruña", "Galicia"),
    "coruna": ("A Coruña", "Galicia"),
    "cuenca": ("Cuenca", "Castilla-La Mancha"),
    "girona": ("Girona", "Cataluña"),
    "granada": ("Granada", "Andalucía"),
    "guadalajara": ("Guadalajara", "Castilla-La Mancha"),
    "guipuzcoa": ("Gipuzkoa", "País Vasco"),
    "gipuzkoa": ("Gipuzkoa", "País Vasco"),
    "huelva": ("Huelva", "Andalucía"),
    "huesca": ("Huesca", "Aragón"),
    "jaen": ("Jaén", "Andalucía"),
    "leon": ("León", "Castilla y León"),
    "lleida": ("Lleida", "Cataluña"),
    "lerida": ("Lleida", "Cataluña"),
    "la rioja": ("La Rioja", "La Rioja"),
    "las palmas": ("Las Palmas", "Canarias"),
    "lleon": ("León", "Castilla y León"),
    "lugo": ("Lugo", "Galicia"),
    "madrid": ("Madrid", "Comunidad de Madrid"),
    "malaga": ("Málaga", "Andalucía"),
    "murcia": ("Murcia", "Región de Murcia"),
    "navarra": ("Navarra", "Comunidad Foral de Navarra"),
    "ourense": ("Ourense", "Galicia"),
    "orense": ("Ourense", "Galicia"),
    "palencia": ("Palencia", "Castilla y León"),
    "pontevedra": ("Pontevedra", "Galicia"),
    "salamanca": ("Salamanca", "Castilla y León"),
    "segovia": ("Segovia", "Castilla y León"),
    "sevilla": ("Sevilla", "Andalucía"),
    "soria": ("Soria", "Castilla y León"),
    "tarragona": ("Tarragona", "Cataluña"),
    "tenerife": ("Santa Cruz de Tenerife", "Canarias"),
    "santa cruz de tenerife": ("Santa Cruz de Tenerife", "Canarias"),
    "teruel": ("Teruel", "Aragón"),
    "toledo": ("Toledo", "Castilla-La Mancha"),
    "valencia": ("Valencia", "Comunidad Valenciana"),
    "valladolid": ("Valladolid", "Castilla y León"),
    "vizcaya": ("Bizkaia", "País Vasco"),
    "bizkaia": ("Bizkaia", "País Vasco"),
    "zamora": ("Zamora", "Castilla y León"),
    "zaragoza": ("Zaragoza", "Aragón"),
    "ceuta": ("Ceuta", "Ceuta"),
    "melilla": ("Melilla", "Melilla"),
}


def _provincia_y_comunidad(valor: Any) -> Tuple[str, str]:
    provincia_normalizada = normalizar_texto(valor)
    provincia_normalizada = provincia_normalizada.replace(" provincia", "").strip()
    resultado = _PROVINCIAS_CCAA.get(provincia_normalizada)
    if resultado:
        return resultado
    return "", ""


def anotar_pendiente(concesion: Dict[str, Any], motivo: str) -> Dict[str, Any]:
    """Devuelve la concesión preservando sus datos y dejando el enriquecimiento pendiente."""
    resultado = dict(concesion)
    resultado.update({
        "domicilio_social": resultado.get("domicilio_social") or "",
        "codigo_postal": resultado.get("codigo_postal") or "",
        "municipio": resultado.get("municipio") or "",
        "clasificacion_territorial": resultado.get("clasificacion_territorial") or "",
        "nivel_confianza": resultado.get("nivel_confianza") or "",
        "estado_verificacion": "PENDIENTE",
        "fuente_verificacion": resultado.get("fuente_verificacion") or "",
        "referencia_verificacion": resultado.get("referencia_verificacion") or "",
        "fecha_verificacion": resultado.get("fecha_verificacion") or "",
        "fecha_dato_fuente": resultado.get("fecha_dato_fuente") or "",
        "motivo_enriquecimiento": motivo,
    })
    return resultado


def _perfil_sin_coincidencia(
    cif: str,
    estado: str,
    motivo: str,
    fecha_verificacion: str,
) -> Dict[str, str]:
    perfil = {campo: "" for campo in CAMPOS_ENRIQUECIMIENTO}
    perfil.update({
        "cif": cif,
        "estado_verificacion": estado,
        "fuente_verificacion": "APIEmpresas.es",
        "referencia_verificacion": f"{API_BASE_URL}/companies?cif={cif}",
        "fecha_verificacion": fecha_verificacion,
        "motivo_enriquecimiento": motivo,
    })
    return perfil


def _extraer_perfil(
    cif: str,
    payload: Dict[str, Any],
    fecha_verificacion: str,
) -> Dict[str, str]:
    if not payload.get("success"):
        return _perfil_sin_coincidencia(
            cif,
            "NO_IDENTIFICADO",
            str(payload.get("message") or payload.get("error") or "Empresa no identificada por la API."),
            fecha_verificacion,
        )

    datos = payload.get("data")
    if not isinstance(datos, dict):
        raise EnriquecimientoAPIError(
            "La respuesta de APIEmpresas no contiene un objeto 'data' válido."
        )

    cif_devuelto = normalizar_cif(datos.get("cif"))
    if not validar_cif(cif_devuelto) or cif_devuelto != cif:
        return _perfil_sin_coincidencia(
            cif,
            "NO_IDENTIFICADO",
            "El CIF devuelto no coincide exactamente con el consultado.",
            fecha_verificacion,
        )

    provincia, comunidad = _provincia_y_comunidad(
        datos.get("province") or datos.get("provincia")
    )
    if not provincia or not comunidad:
        perfil = _perfil_sin_coincidencia(
            cif,
            "PENDIENTE",
            "Empresa identificada, pero APIEmpresas no devolvió una provincia reconocida.",
            fecha_verificacion,
        )
    else:
        andalucia = comunidad == "Andalucía"
        perfil = _perfil_sin_coincidencia(
            cif,
            "VERIFICADO",
            "CIF coincidente y provincia devuelta por APIEmpresas; clasificación basada en provincia.",
            fecha_verificacion,
        )
        perfil.update({
            "provincia": provincia,
            "comunidad_autonoma": comunidad,
            "clasificacion_territorial": (
                "DOMICILIO_ANDALUCIA" if andalucia else "DOMICILIO_FUERA_ANDALUCIA"
            ),
            "presencia_andalucia": "Si" if andalucia else "No",
            "nivel_confianza": "ALTA (CIF exacto y provincia de API)",
        })

    perfil["empresa_registral"] = str(datos.get("name") or datos.get("razon_social") or "")
    perfil["domicilio_social"] = str(
        datos.get("address") or datos.get("registered_address") or ""
    )
    perfil["codigo_postal"] = str(
        datos.get("postal_code") or datos.get("postcode") or datos.get("zip_code") or ""
    )
    perfil["municipio"] = str(
        datos.get("municipality") or datos.get("city") or datos.get("municipio") or ""
    )
    fuentes = datos.get("sources")
    registro = fuentes.get("registry") if isinstance(fuentes, dict) else None
    fecha_fuente = registro.get("as_of") if isinstance(registro, dict) else None
    perfil["fecha_dato_fuente"] = str(fecha_fuente or datos.get("updated_at") or "")
    perfil["referencia_verificacion"] = str(
        (registro.get("url_pdf") if isinstance(registro, dict) else "")
        or f"{API_BASE_URL}/companies?cif={cif}"
    )
    return perfil


def _consultar_cupo(session: requests.Session, api_key: str) -> int:
    respuesta = session.get(
        f"{API_BASE_URL}/usage",
        headers={"X-API-KEY": api_key},
        timeout=API_TIMEOUT,
    )
    if respuesta.status_code in (401, 403):
        raise EnriquecimientoConfigurationError(
            f"APIEmpresas rechazó la credencial (HTTP {respuesta.status_code})."
        )
    respuesta.raise_for_status()
    try:
        cupo = respuesta.json()["data"]["stats"]["remaining_calls"]
        return max(0, int(cupo))
    except (KeyError, TypeError, ValueError) as error:
        raise EnriquecimientoAPIError(
            "La API de uso no devolvió data.stats.remaining_calls."
        ) from error


def _consultar_empresa(
    session: requests.Session,
    api_key: str,
    cif: str,
    fecha_verificacion: str,
) -> Dict[str, str]:
    respuesta = session.get(
        f"{API_BASE_URL}/companies",
        params={"cif": cif},
        headers={"X-API-KEY": api_key},
        timeout=API_TIMEOUT,
    )
    if respuesta.status_code in (401, 403):
        raise EnriquecimientoConfigurationError(
            f"APIEmpresas rechazó la credencial (HTTP {respuesta.status_code})."
        )
    if respuesta.status_code == 404:
        return _perfil_sin_coincidencia(
            cif,
            "NO_IDENTIFICADO",
            "No se encontró una empresa para el CIF consultado.",
            fecha_verificacion,
        )
    if respuesta.status_code in (402, 429):
        retry_after = respuesta.headers.get("Retry-After")
        detalle_reintento = (
            f"; Retry-After: {retry_after}" if retry_after else ""
        )
        raise ConsultaNoDisponibleError(
            "APIEmpresas rechazó temporalmente la consulta por cuota o límite "
            f"(HTTP {respuesta.status_code}){detalle_reintento}."
        )
    respuesta.raise_for_status()
    try:
        payload = respuesta.json()
    except ValueError as error:
        raise EnriquecimientoAPIError(
            "APIEmpresas devolvió una respuesta que no es JSON válido."
        ) from error
    if not isinstance(payload, dict):
        raise EnriquecimientoAPIError(
            "APIEmpresas devolvió una estructura JSON inesperada."
        )
    return _extraer_perfil(cif, payload, fecha_verificacion)


def _leer_cache(ruta: Path) -> Dict[str, Dict[str, str]]:
    if not ruta.exists():
        return {}
    with ruta.open("r", encoding="utf-8-sig", newline="") as archivo:
        lector = csv.DictReader(archivo)
        if not lector.fieldnames:
            raise EnriquecimientoAPIError(
                f"El CSV de enriquecimiento no tiene cabeceras: {ruta}"
            )
        return {
            normalizar_cif(fila.get("cif")): {
                campo: (fila.get(campo) or "")
                for campo in CAMPOS_ENRIQUECIMIENTO
            }
            for fila in lector
            if normalizar_cif(fila.get("cif"))
        }


def _guardar_cache(ruta: Path, perfiles: Dict[str, Dict[str, str]]) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    with temporal.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=CAMPOS_ENRIQUECIMIENTO)
        escritor.writeheader()
        for cif in sorted(perfiles):
            escritor.writerow({
                campo: perfiles[cif].get(campo, "")
                for campo in CAMPOS_ENRIQUECIMIENTO
            })
    temporal.replace(ruta)


def _guardar_concesiones_enriquecidas(
    ruta: Path,
    concesiones: Sequence[Dict[str, Any]],
) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    temporal = ruta.with_suffix(ruta.suffix + ".tmp")
    with temporal.open("w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(
            archivo,
            fieldnames=_CAMPOS_CONCESIONES_ENRIQUECIDAS,
            extrasaction="ignore",
        )
        escritor.writeheader()
        escritor.writerows(concesiones)
    temporal.replace(ruta)


def _seleccionar_cifs(
    concesiones: Sequence[Dict[str, Any]],
    cifs_pendientes: Set[str],
    limite: int,
) -> List[str]:
    """Intercala CIF por convocatoria para evitar consumir el cupo con un solo organismo."""
    codigos = ("834244", "848058")
    grupos: Dict[str, List[str]] = {codigo: [] for codigo in codigos}
    vistos_por_grupo: Dict[str, Set[str]] = {codigo: set() for codigo in codigos}
    sin_codigo: List[str] = []
    vistos_sin_codigo: Set[str] = set()

    for concesion in concesiones:
        cif = normalizar_cif(concesion.get("cif"))
        if cif not in cifs_pendientes:
            continue
        codigo = str(concesion.get("codigo_bdns") or "").strip()
        if codigo in grupos:
            if cif not in vistos_por_grupo[codigo]:
                grupos[codigo].append(cif)
                vistos_por_grupo[codigo].add(cif)
        elif cif not in vistos_sin_codigo:
            sin_codigo.append(cif)
            vistos_sin_codigo.add(cif)

    seleccionados: List[str] = []
    vistos: Set[str] = set()
    while len(seleccionados) < limite:
        hubo_cambio = False
        for codigo in codigos:
            for cif in grupos[codigo]:
                if cif not in vistos:
                    seleccionados.append(cif)
                    vistos.add(cif)
                    hubo_cambio = True
                    break
                # Cada iteración consume como máximo un CIF por convocatoria.
            if len(seleccionados) >= limite:
                break
        if len(seleccionados) >= limite:
            break
        siguiente = next((cif for cif in sin_codigo if cif not in vistos), None)
        if siguiente:
            seleccionados.append(siguiente)
            vistos.add(siguiente)
            hubo_cambio = True
        if not hubo_cambio:
            break
    return seleccionados


def enriquecer_concesiones(
    concesiones: Sequence[Dict[str, Any]],
    *,
    cache_path: Path = CACHE_PATH,
    api_key: Optional[str] = None,
    max_consultas: int = 100,
    session: Optional[requests.Session] = None,
    fecha_verificacion: Optional[str] = None,
    output_path: Optional[Path] = CONCESIONES_ENRIQUECIDAS_PATH,
) -> List[Dict[str, Any]]:
    """Enriquece cada CIF único hasta el cupo autorizado y conserva el resto pendiente."""
    fecha = fecha_verificacion or date.today().isoformat()
    ruta_cache = Path(cache_path)
    perfiles = _leer_cache(ruta_cache)

    cifs_pendientes: Set[str] = set()
    for concesion in concesiones:
        cif = normalizar_cif(concesion.get("cif"))
        if not validar_cif(cif):
            continue
        if cif not in perfiles:
            cifs_pendientes.add(cif)

    if cifs_pendientes:
        clave = api_key or os.getenv("APIEMPRESAS_API_KEY")
        if not clave:
            raise EnriquecimientoConfigurationError(
                "Configura APIEMPRESAS_API_KEY en el entorno para enriquecer los CIF."
            )

        cliente_http = session or requests.Session()
        cupo = _consultar_cupo(cliente_http, clave)
        if cupo:
            time.sleep(INTERVALO_CONSULTA_SEGUNDOS)
        limite = min(max(0, int(max_consultas)), cupo)
        seleccionados = _seleccionar_cifs(concesiones, cifs_pendientes, limite)
        log.info(
            "Enriquecimiento APIEmpresas: %s CIF únicos pendientes; cupo permitido: %s.",
            len(cifs_pendientes),
            len(seleccionados),
        )

        consultados = 0
        motivo_no_consultados = (
            "No consultado en esta ejecución: fuera del cupo o límite configurado."
        )
        for cif in seleccionados:
            if consultados:
                time.sleep(INTERVALO_CONSULTA_SEGUNDOS)
            try:
                perfiles[cif] = _consultar_empresa(
                    cliente_http,
                    clave,
                    cif,
                    fecha,
                )
            except ConsultaNoDisponibleError as error:
                motivo_no_consultados = (
                    "Consulta no completada por límite temporal o de cuota de "
                    "APIEmpresas; reintentar en otra ejecución."
                )
                log.warning("%s; el resto del lote seguirá pendiente.", error)
                break
            _guardar_cache(ruta_cache, perfiles)
            consultados += 1
        log.info("Consultas de empresa realizadas: %s.", consultados)

    resultados: List[Dict[str, Any]] = []
    for concesion in concesiones:
        cif = normalizar_cif(concesion.get("cif"))
        if not validar_cif(cif):
            perfil = _perfil_sin_coincidencia(
                cif,
                "NO_IDENTIFICADO",
                "CIF ausente o inválido; no se consultó la API.",
                fecha,
            )
        elif cif in perfiles:
            perfil = perfiles[cif]
        else:
            perfil = _perfil_sin_coincidencia(
                cif,
                "PENDIENTE",
                motivo_no_consultados,
                fecha,
            )

        resultado = dict(concesion)
        resultado.update(perfil)
        if perfil.get("estado_verificacion") == "VERIFICADO":
            resultado["provincia"] = perfil.get("provincia", "")
            resultado["comunidad_autonoma"] = perfil.get("comunidad_autonoma", "")
            resultado["presencia_andalucia"] = perfil.get("presencia_andalucia", "")
            resultado["empresa_registral"] = perfil.get("empresa_registral", "")
        resultados.append(resultado)

    if output_path is not None:
        _guardar_concesiones_enriquecidas(Path(output_path), resultados)

    conteos = {
        estado: sum(
            registro.get("estado_verificacion") == estado
            for registro in resultados
        )
        for estado in ("VERIFICADO", "PENDIENTE", "NO_IDENTIFICADO")
    }
    log.info(
        "Resultado territorial por concesión: %s.",
        ", ".join(f"{estado}={cantidad}" for estado, cantidad in conteos.items()),
    )
    return resultados
