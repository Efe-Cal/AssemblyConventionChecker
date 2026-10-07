"""Source-level System V AMD64 ABI checks for GNU AT&T assembly."""

from .analyzer import analyze
from .model import AnalysisReport, Diagnostic, FunctionCoverage, SourceLocation

__all__ = ["analyze", "AnalysisReport", "Diagnostic", "FunctionCoverage", "SourceLocation"]
