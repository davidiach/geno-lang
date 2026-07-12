"""
Analysis Framework
==================

Tools for analyzing and visualizing benchmark experiment results.

Usage:
    from analysis import ResultsAnalyzer, ReportGenerator

    analyzer = ResultsAnalyzer("results/experiment_001")
    analyzer.run_all_analyses()

    generator = ReportGenerator(analyzer)
    generator.generate_full_report("report.pdf")
"""

from analysis.analyzer import ResultsAnalyzer
from analysis.report_generator import ReportGenerator
from analysis.statistics import StatisticalTests
from analysis.visualizations import Visualizer

__all__ = [
    "ReportGenerator",
    "ResultsAnalyzer",
    "StatisticalTests",
    "Visualizer",
]
