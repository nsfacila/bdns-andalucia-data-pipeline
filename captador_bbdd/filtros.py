"""
Reglas de clasificación de concesiones para las fases posteriores a la extracción.

La ubicación se determina solo con datos empresariales explícitos. Las concesiones
sin ubicación quedan pendientes de enriquecimiento; NUTS y CIF no son domicilio.
"""

import unicodedata
import re
from typing import Dict, Any, Optional

ANIO_MINIMO = 2024

CODIGOS_BDNS_VALIDOS = {"834244": "CDTI", "848058": "Red.es"}

# Organismos permitidos
ORGANISMOS_VALIDOS = ["cdti", "red.es", "red es", "redes"]

# Palabras clave por programa
PALABRAS_CLAVE_PROGRAMAS = [
    # CDTI
    "misiones",
    "cervera",
    "innoglobal",
    "neotec",
    "interconecta",
    "innterconecta",
    "step",
    "cppi",
    "pid",
    
    # Red.es
    "kit digital",
    "espacios de datos",
    "inteligencia artificial",
    "catedras ia",
    "tecnologias duales"
]

PROVINCIAS_ANDALUCIA = [
    "almeria", "cadiz", "cordoba", "granada",
    "huelva", "jaen", "malaga", "sevilla"
]

PREFIXES_CP_ANDALUCIA = ("04", "11", "14", "18", "21", "23", "29", "41")
CODIGO_POSTAL_ES = re.compile(r"^\d{5}$")
CODIGO_NUTS_ES = re.compile(r"^ES(?:\d{0,3}|\s*[-–].*)$", re.IGNORECASE)

VALORES_TRUE = {"si", "sí", "true", "1"}
VALORES_FALSE = {"no", "false", "0"}
PROVINCIAS_ESPANA = {
    "alava", "albacete", "alicante", "almeria", "asturias", "avila", "badajoz",
    "barcelona", "burgos", "caceres", "cadiz", "cantabria", "castellon",
    "ciudad real", "cordoba", "cuenca", "girona", "granada", "guadalajara",
    "guipuzcoa", "huelva", "huesca", "jaen", "a coruna", "la coruna",
    "la rioja", "las palmas", "leon", "lleida", "lugo", "madrid", "malaga",
    "murcia", "navarra", "ourense", "palencia", "pontevedra", "salamanca",
    "segovia", "sevilla", "soria", "tarragona", "tenerife", "teruel", "toledo",
    "valencia", "valladolid", "vizcaya", "zamora", "zaragoza",
}
COMUNIDADES_AUTONOMAS_ESPANA = {
    "andalucia", "aragon", "principado de asturias", "asturias",
    "islas baleares", "baleares", "canarias", "cantabria",
    "castilla la mancha", "castilla y leon", "cataluna",
    "comunidad valenciana", "valencia", "extremadura", "galicia",
    "comunidad de madrid", "madrid", "region de murcia", "murcia",
    "comunidad foral de navarra", "navarra", "pais vasco", "la rioja",
    "ceuta", "melilla",
}


def normalizar_texto(texto: Optional[str]) -> str:
    """Convierte a minúsculas y elimina tildes/diacríticos."""
    if not texto:
        return ""
    texto_min = str(texto).lower().strip()
    nfkd = unicodedata.normalize('NFD', texto_min)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def es_organismo_valido(concesion: Dict[str, Any]) -> bool:
    """Valida organismo o código BDNS dentro del alcance del proyecto."""
    codigo_bdns = str(
        concesion.get("codigo_bdns")
        or concesion.get("numeroConvocatoria")
        or ""
    ).strip()
    if codigo_bdns:
        organismo_esperado = CODIGOS_BDNS_VALIDOS.get(codigo_bdns)
        if organismo_esperado is None:
            return False
        organismo_recibido = normalizar_texto(concesion.get("organismo"))
        if not organismo_recibido:
            return True
        if organismo_esperado == "CDTI":
            return not any(alias in organismo_recibido for alias in ("red.es", "red es", "redes"))
        return "cdti" not in organismo_recibido

    organismo = normalizar_texto(concesion.get("organismo"))
    convocatoria = normalizar_texto(concesion.get("convocatoria"))
    
    texto = f"{organismo} {convocatoria}"
    return any(org in texto for org in ORGANISMOS_VALIDOS)


def coincide_programa_clave(concesion: Dict[str, Any]) -> bool:
    """Valida que el título o programa contenga alguna palabra clave prioritaria."""
    convocatoria = normalizar_texto(concesion.get("convocatoria"))
    proyecto = normalizar_texto(concesion.get("proyecto"))
    
    texto = f"{convocatoria} {proyecto}"
    return any(kw in texto for kw in PALABRAS_CLAVE_PROGRAMAS)


def es_ubicacion_andalucia(concesion: Dict[str, Any]) -> Optional[bool]:
    """
    Devuelve True/False solo con ubicación empresarial verificada; si no, None.

    El código NUTS describe el alcance territorial de la ayuda, no el domicilio
    del beneficiario. El CIF tampoco codifica una provincia fiable.
    """
    estado_verificacion = normalizar_texto(
        concesion.get("estado_verificacion")
    )
    if estado_verificacion != "verificado":
        return None

    ccaa = normalizar_texto(concesion.get("comunidad_autonoma"))
    provincia = normalizar_texto(concesion.get("provincia"))
    presencia = normalizar_texto(concesion.get("presencia_andalucia"))
    cp = str(concesion.get("codigo_postal", "")).strip()

    if presencia in VALORES_TRUE:
        return True
    if presencia in VALORES_FALSE:
        return False

    provincia_andaluza = provincia in PROVINCIAS_ANDALUCIA
    if (
        ccaa == "andalucia"
        or provincia_andaluza
        or CODIGO_POSTAL_ES.fullmatch(cp)
        and cp.startswith(PREFIXES_CP_ANDALUCIA)
    ):
        return True

    ccaa_fuera_de_andalucia = (
        ccaa in COMUNIDADES_AUTONOMAS_ESPANA and ccaa != "andalucia"
    )
    provincia_fuera_de_andalucia = (
        provincia in PROVINCIAS_ESPANA and provincia not in PROVINCIAS_ANDALUCIA
    )
    codigo_postal_fuera_de_andalucia = (
        CODIGO_POSTAL_ES.fullmatch(cp)
        and not cp.startswith(PREFIXES_CP_ANDALUCIA)
    )
    if (
        ccaa_fuera_de_andalucia
        or provincia_fuera_de_andalucia
        or codigo_postal_fuera_de_andalucia
    ):
        return False

    return None


def es_anio_valido(fecha_str: str) -> bool:
    """Valida que la fecha/año sea 2024 o posterior."""
    if not fecha_str:
        return False
    try:
        anio = int(str(fecha_str).strip()[:4])
        return anio >= ANIO_MINIMO
    except (ValueError, IndexError):
        return False


def evaluar_subvencion(concesion: Dict[str, Any]) -> Dict[str, Any]:
    """Evalúa fecha, organismo, temática y ubicación enriquecida del beneficiario."""
    # 1. Filtro de Año
    if not es_anio_valido(concesion.get("fecha_concesion")):
        return {"estado": "descartada", "motivo": f"Año fuera de rango (< {ANIO_MINIMO})"}

    # 2. Filtro de Organismo (Obligatorio CDTI o Red.es)
    if not es_organismo_valido(concesion):
        return {"estado": "descartada", "motivo": "Organo no válido (no es CDTI ni Red.es)"}

    # 3. Filtro de Palabras Clave de Programa
    if not coincide_programa_clave(concesion):
        return {"estado": "descartada", "motivo": "Programa no prioritario"}

    # 4. Filtro de Andalucía
    ubicacion_andalucia = es_ubicacion_andalucia(concesion)
    if ubicacion_andalucia is None:
        return {
            "estado": "pendiente_enriquecimiento",
            "motivo": (
                "La ubicación no está verificada; se requiere evidencia empresarial "
                "contrastada. No se clasifica por NUTS ni por CIF."
            ),
        }
    if not ubicacion_andalucia:
        return {"estado": "descartada", "motivo": "Ubicación fuera de Andalucía"}

    return {"estado": "aceptada", "motivo": "Cumple todos los criterios"}