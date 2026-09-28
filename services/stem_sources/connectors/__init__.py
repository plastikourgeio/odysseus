from .arxiv import ArxivConnector
from .crossref import CrossrefConnector
from .openalex import OpenAlexConnector
from .plos import PlosConnector
from .semantic_scholar import SemanticScholarConnector

CONNECTOR_CLASSES = {
    "arxiv": ArxivConnector,
    "crossref": CrossrefConnector,
    "openalex": OpenAlexConnector,
    "plos": PlosConnector,
    "semantic_scholar": SemanticScholarConnector,
}

__all__ = ["CONNECTOR_CLASSES"]
