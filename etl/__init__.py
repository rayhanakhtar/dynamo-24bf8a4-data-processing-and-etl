"""ETL oracle pipeline package (development home; packaged into task/solution)."""

from etl.pipeline import PipelineResult, pipeline_main, run
from etl.rules import Dictionary, load

__all__ = ["PipelineResult", "pipeline_main", "run", "Dictionary", "load"]