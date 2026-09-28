from __future__ import annotations

import asyncio
import logging
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .connectors import CONNECTOR_CLASSES
from .connectors.base import ProviderUnavailable
from .evidence import deduplicate_evidence, rank_evidence, rerank_evidence_for_query
from .glossary import build_english_technical_query
from .language import detect_language, fold_text, preferred_response_language
from .models import EvidenceItem, QueryPlan, SourceSpec
from .policy import RetrievalDecision, decide_retrieval
from .registry import SourceRegistry

logger = logging.getLogger(__name__)

# Patterns are matched against accent-folded text.
_INTENT_SCORING = [
    # Strong failure evidence should beat a generic request to change a setting.
    ("troubleshoot", r"\b(error|fail|failed|fails|falla|fallo|problema|no funciona|not working|bug|troubleshoot|diagnos|depur)\b", 4),
    ("troubleshoot", r"\b(se levanta|se despega|no gira|no enciende|no arranca|no conecta|no compila|no detecta|not detected|not detect|atasc|clog)\b", 4),
    ("troubleshoot", r"\b(has|have|tiene|presenta).{0,40}\b(warping|stringing|alabeo|subextrusion|sobreextrusion)\b", 4),
    ("troubleshoot", r"\b(warping|stringing|under.?extrusion|over.?extrusion)\b", 1),
    ("purchase", r"\b(buy|comprar|precio|price|stock|tienda|shop|where can i find|donde.*compr)\b", 4),
    ("configure", r"\b(setting|settings|ajuste|ajustes|configur|slicer|perfil|profile|nozzle|boquilla|bed temperature|temperatura de cama|retraction|retracci)\b", 3),
    ("compare", r"\b(compare|comparar|comparacion|versus| vs\.? |mejor que|diferencia|difference|which.*better|cual.*mejor)\b", 4),
    ("research", r"\b(paper|papers|arxiv|research|investigacion|study|studies|estudio|evidence|evidencia|literature|literatura|fuentes|source)\b", 4),
    ("code", r"\b(code|coding|codigo|python|c\+\+|javascript|compile|compil|compiler|function|funcion|script|library|libreria|variable|bucle|loop)\b", 3),
    ("design", r"\b(design|disen|arquitectura|architecture|build|construir|prototype|prototip|circuit|circuito|bom|dimensionar)\b", 3),
    ("brainstorm", r"\b(idea|ideas|brainstorm|what if|que tal si|podriamos|could we|quiero hacer|se me ocurre)\b", 2),
]

_INTENT_TIEBREAK = [
    "troubleshoot", "purchase", "configure", "compare", "research", "code", "design", "brainstorm", "explain"
]


_DOMAIN_RULES = {
    "electronics": r"\b(arduino|esp32|micro:bit|sensor|gpio|i2c|spi|uart|pwm|voltage|voltaje|tension|resistor|resistencia|breadboard|electronica|placa|pinout|patillaje)\b",
    "embedded": r"\b(microcontroller|microcontrolador|firmware|embedded|embebid|arduino|esp32|stm32|giga r1)\b",
    "computing": r"\b(python|linux|raspberry pi|docker|ssh|api|database|software|computer|computador|ordenador|programacion|coding|javascript|c\+\+|libreria|library|compil|debug|depur)\b",
    "networking_security": r"\b(wifi|wi-fi|bluetooth|network|red|tcp|ip|ssh|cyber|ciber|security|seguridad|encrypt|cifrad|encript)\b",
    "robotics": r"\b(robots?|servo|motor|autonomous|autonom|lidar|line follower|seguidor de linea|obstacles?|obstaculos?|ros\b|webots)\b",
    "fabrication": r"\b(3d print|3d printing|impresion 3d|printer|impresora|slicer|creality|ender|filament|filamento|stl|3mf|cad|warping|stringing|infill|support|soporte|boquilla|cama de impresion|primera capa|retraccion)\b",
    "mechanical_engineering": r"\b(gear|engranaje|torque|par motor|bearing|rodamiento|mechanism|mecanismo|tolerance|tolerancia|load|carga|structure|estructura|backlash|holgura)\b",
    "physics": r"\b(force|fuerza|energy|energia|motion|movimiento|wave|onda|optics|optica|electricity|electricidad|magnet|thermodynamic|termodinamic|pressure|presion|aceleracion)\b",
    "chemistry_materials": r"\b(chemistry|quimica|polymer|polimero|material|petg|pla|abs|nylon|resin|resina|battery chemistry|corrosion|corrosion)\b",
    "biology": r"\b(biology|biologia|cell|celula|genetic|genetica|ecology|ecologia|plant|planta|animal|microbi)\b",
    "mathematics_statistics": r"\b(math|matemat|algebra|geometry|geometria|trigon|probability|probabilidad|statistics|estadist|graph theory|teoria de grafos|function|funcion|desviacion estandar)\b",
    "astronomy_space": r"\b(astronomy|astronomia|planet|planeta|star|estrella|telescope|telescopio|satellite|satelite|space|espacio|orbit|orbita)\b",
    "aerospace": r"\b(rocket|cohete|aerodynamic|aerodinamic|aircraft|avion|flight|vuelo|cansat|astro pi)\b",
    "earth_science": r"\b(weather|meteorolog|climate|clima|geology|geolog|ocean|ocean|earth science|ciencias de la tierra|sism|atmosphere|atmosfera|humedad)\b",
    "ai_data": r"\b(ai\b|artificial intelligence|inteligencia artificial|machine learning|aprendizaje automatico|ml\b|neural|computer vision|vision por computador|dataset|data science|ciencia de datos|modelo|model|sesgo)\b",
    "measurement": r"\b(calibrat|calibracion|measure|medir|measurement|medicion|accuracy|exactitud|precision|uncertainty|incertidumbre|multimeter|multimetro)\b",
    "engineering_design": r"\b(requirement|requisito|trade.?off|constraint|restriccion|prototype|prototip|bom|architecture|arquitectura|design|disen|hipotesis|test|prueba)\b",
}


class STEMSourceRouter:
    def __init__(self, registry: Optional[SourceRegistry] = None):
        self.registry = registry or SourceRegistry()

    def infer_intent(self, query: str) -> str:
        text = fold_text(query)
        scores = {name: 0 for name in _INTENT_TIEBREAK}
        for intent, pattern, weight in _INTENT_SCORING:
            if re.search(pattern, text, re.I):
                scores[intent] += weight
        best = max(scores.values())
        if best <= 0:
            return "explain"
        for intent in _INTENT_TIEBREAK:
            if scores[intent] == best:
                return intent
        return "explain"

    def infer_domains(self, query: str) -> List[str]:
        text = fold_text(query)
        found = [name for name, pattern in _DOMAIN_RULES.items() if re.search(pattern, text, re.I)]
        return found or ["multidisciplinary"]

    @staticmethod
    def _unique_queries(values: Sequence[str]) -> Tuple[str, ...]:
        out: List[str] = []
        seen = set()
        for value in values:
            value = (value or "").strip()
            if not value:
                continue
            key = fold_text(value)
            if key not in seen:
                seen.add(key)
                out.append(value)
        return tuple(out)

    def retrieval_policy(self, query: str, *, intent: Optional[str] = None,
                         domains: Optional[Iterable[str]] = None) -> RetrievalDecision:
        intent = intent or self.infer_intent(query)
        domain_list = tuple(domains or self.infer_domains(query))
        return decide_retrieval(query, intent=intent, domains=domain_list)

    def plan_query(
        self,
        query: str,
        *,
        response_language: Optional[str] = None,
        intent_override: Optional[str] = None,
    ) -> QueryPlan:
        # Structured scholarly routes may deliberately remove transport words
        # such as ``paper``/``journal`` before this point.  Keep the routing
        # intent explicit so the query planner does not silently fall back to
        # ``explain`` and inject terms such as ``explanation`` into arXiv.
        intent = (intent_override or self.infer_intent(query)).strip().casefold()
        if intent not in _INTENT_TIEBREAK:
            intent = self.infer_intent(query)
        domains = tuple(self.infer_domains(query))
        detected = detect_language(query)
        response = response_language or preferred_response_language(query)
        english_query, terms = build_english_technical_query(query, intent=intent, domains=domains)

        if detected in {"es", "mixed"}:
            # Official/general search keeps the user's wording first; scholarly
            # search prefers English technical terminology, then falls back to Spanish.
            official = self._unique_queries([query, english_query])
            general = self._unique_queries([query, english_query])
            scholarly = self._unique_queries([english_query, query])
        elif detected == "en":
            official = self._unique_queries([query])
            general = self._unique_queries([query])
            scholarly = self._unique_queries([query])
        else:
            official = self._unique_queries([query, english_query])
            general = self._unique_queries([query, english_query])
            scholarly = self._unique_queries([english_query, query])

        return QueryPlan(
            original_query=query,
            detected_language=detected,
            response_language=response,
            intent=intent,
            domains=domains,
            official_queries=official,
            scholarly_queries=scholarly,
            general_queries=general,
            technical_terms=terms,
        )

    @staticmethod
    def _source_affinity(query: str, spec: SourceSpec) -> int:
        text = fold_text(query)
        score = 0

        if "arduino" in text or "modulino" in text or "giga r1" in text:
            if spec.id == "arduino_docs":
                score += 120
            elif spec.id == "manufacturer_docs":
                score += 35
            elif spec.id == "raspberry_pi_docs":
                score -= 90

        if "raspberry pi" in text:
            if spec.id == "raspberry_pi_docs":
                score += 120
            elif spec.id == "manufacturer_docs":
                score += 30
            elif spec.id == "arduino_docs":
                score -= 90

        if any(term in text for term in ("ender", "creality", "prusa", "bambu", "impresion 3d", "3d print")):
            if "creality" in text or "ender" in text:
                if spec.id == "creality_docs":
                    score += 135
            if "prusa" in text and spec.id == "prusa_docs":
                score += 135
            if "bambu" in text and spec.id == "bambu_docs":
                score += 135
            if spec.id == "manufacturer_docs":
                score += 100
            elif spec.id == "official_repo":
                score += 55
            elif spec.id == "community":
                score += 10

        if any(term in text for term in ("esp32", "esp8266", "espressif", "esp-idf")):
            if spec.id == "espressif_docs":
                score += 135
            elif spec.id == "manufacturer_docs":
                score += 35

        if "micro:bit" in text or "microbit" in text or "makecode" in text:
            if spec.id == "microbit_docs":
                score += 135

        if "klipper" in text and spec.id == "klipper_docs":
            score += 135
        if "marlin" in text and spec.id == "marlin_docs":
            score += 135
        if "octoprint" in text and spec.id == "octoprint_docs":
            score += 135
        if "webots" in text and spec.id == "webots_docs":
            score += 135
        if "python" in text and spec.id == "python_docs":
            score += 100

        return score

    def recommend_sources(self, query: str, *, intent: Optional[str] = None,
                          domains: Optional[Iterable[str]] = None,
                          limit: int = 10) -> List[SourceSpec]:
        intent = intent or self.infer_intent(query)
        domain_list = list(domains or self.infer_domains(query))
        base = self.registry.match(domain_list, intent)
        ranked = sorted(
            enumerate(base),
            key=lambda row: (-(self._source_affinity(query, row[1])), row[0]),
        )
        return [spec for _, spec in ranked][:max(1, limit)]

    async def retrieve_scholarly(self, query: str, *, limit_per_source: int = 5,
                                 max_connectors: int = 4,
                                 bilingual_fallback: bool = True,
                                 min_results_before_fallback: int = 2,
                                 intent_override: Optional[str] = None) -> List[EvidenceItem]:
        """
        Search scholarly connectors with quota-conscious bilingual fallback.

        Spanish/mixed prompts use the English technical query first because most
        scholarly indexes are English-dominant. The original Spanish query is
        only attempted for a connector when the primary query yields fewer than
        ``min_results_before_fallback`` results. This avoids doubling API usage
        by default while still preserving bilingual recovery.
        """
        plan = self.plan_query(query, intent_override=intent_override)
        specs = [s for s in self.registry.match(plan.domains, plan.intent) if s.connector]
        if not specs:
            specs = [s for s in self.registry.connected() if s.kind in {"scholarly", "scholarly_index"}]

        connectors: List[Tuple[SourceSpec, object]] = []
        for spec in specs:
            cls = CONNECTOR_CLASSES.get(spec.connector or "")
            if not cls:
                continue
            connector = cls()
            if not connector.available():
                logger.info("STEM connector unavailable (missing optional credential): %s", spec.id)
                continue
            connectors.append((spec, connector))
            if len(connectors) >= max_connectors:
                break

        if not connectors:
            return []

        primary_query = plan.scholarly_queries[0] if plan.scholarly_queries else query
        primary_tasks = [asyncio.create_task(c.search(primary_query, limit_per_source)) for _, c in connectors]
        primary_results = await asyncio.gather(*primary_tasks, return_exceptions=True)

        collected: Dict[str, List[EvidenceItem]] = {}
        failed_sources = set()
        fallback_jobs: List[Tuple[str, asyncio.Task]] = []
        fallback_query = plan.scholarly_queries[1] if len(plan.scholarly_queries) > 1 else ""

        for (spec, connector), result in zip(connectors, primary_results):
            if isinstance(result, Exception):
                # A transport/provider failure is not the same as a successful
                # zero-result search.  Do not immediately call the same provider
                # again with the bilingual fallback.
                if isinstance(result, ProviderUnavailable):
                    logger.info("STEM source %s temporarily unavailable: %s", spec.id, result)
                else:
                    logger.warning("STEM source %s failed: %s", spec.id, result)
                failed_sources.add(spec.id)
                rows: List[EvidenceItem] = []
            else:
                rows = list(result)
            collected[spec.id] = rows

            if (
                spec.id not in failed_sources
                and bilingual_fallback
                and fallback_query
                and fallback_query != primary_query
                and len(rows) < max(0, int(min_results_before_fallback))
            ):
                fallback_jobs.append((spec.id, asyncio.create_task(connector.search(fallback_query, limit_per_source))))

        if fallback_jobs:
            fallback_results = await asyncio.gather(*(task for _, task in fallback_jobs), return_exceptions=True)
            for (source_id, _), result in zip(fallback_jobs, fallback_results):
                if isinstance(result, Exception):
                    logger.warning("STEM bilingual fallback %s failed: %s", source_id, result)
                    continue
                collected[source_id].extend(result)

        out: List[EvidenceItem] = []
        for rows in collected.values():
            out.extend(rows)
        return rerank_evidence_for_query(
            deduplicate_evidence(out),
            primary_query,
            technical_terms=plan.technical_terms,
        )
