# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Data export module of the survey analysis tool: exports prepared data as JSON
and CSV.
"""

import json
import logging
from pathlib import Path
from typing import Optional, Any
from datetime import datetime

import pandas as pd
from .csv_safety import safe_dataframe

logger = logging.getLogger(__name__)


class DataExporter:
    """Exports analysis results in several formats."""
    
    def __init__(self, output_dir: Path):
        """
        Initialises the DataExporter.
        
        Args:
            output_dir: base output directory
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _serialize_value(self, value: Any) -> Any:
        """Converts values for the JSON serialisation."""
        import numpy as np
        
        if isinstance(value, pd.DataFrame):
            return value.to_dict(orient='index')
        if isinstance(value, pd.Series):
            return value.to_dict()
        if hasattr(value, 'tolist'):  # numpy arrays
            return value.tolist()
        if isinstance(value, (datetime,)):
            return value.isoformat()
        # numpy scalar types
        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.bool_):
            return bool(value)
        if pd.isna(value):
            return None
        return value
    
    def export_json(
        self,
        data: dict,
        filename: str,
        subdir: Optional[str] = None
    ) -> Path:
        """
        Exports data as JSON.
        
        Args:
            data: dictionary with the data
            filename: file name (without extension)
            subdir: optional subdirectory
        
        Returns:
            path of the saved file
        """
        output_path = self.output_dir
        if subdir:
            output_path = output_path / subdir
            output_path.mkdir(parents=True, exist_ok=True)
        
        filepath = output_path / f"{filename}.json"
        
        # serialise the data
        serialized = {}
        for key, value in data.items():
            serialized[key] = self._serialize_value(value)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(serialized, f, ensure_ascii=False, indent=2)
        
        logger.info(f"JSON exported: {filepath}")
        return filepath
    
    def export_csv(
        self,
        data: pd.DataFrame | dict,
        filename: str,
        subdir: Optional[str] = None
    ) -> Path:
        """
        Exports data as CSV.
        
        Args:
            data: DataFrame or dictionary
            filename: file name (without extension)
            subdir: optional subdirectory
        
        Returns:
            path of the saved file
        """
        output_path = self.output_dir
        if subdir:
            output_path = output_path / subdir
            output_path.mkdir(parents=True, exist_ok=True)
        
        filepath = output_path / f"{filename}.csv"
        
        if isinstance(data, dict):
            df = pd.DataFrame(data)
        else:
            df = data
        
        safe_dataframe(df).to_csv(filepath, index=True, encoding='utf-8-sig', sep=';')
        
        logger.info(f"CSV exported: {filepath}")
        return filepath
    
    def export_question_result(
        self,
        question_id: str,
        question_config: dict,
        analysis_result: dict,
        formats: list[str] = None
    ) -> dict[str, Path]:
        """
        Exports the result of a question analysis.
        
        Args:
            question_id: id of the question
            question_config: configuration
            analysis_result: analysis result
            formats: export formats (default: ["json"])
        
        Returns:
            dictionary {format: file path}
        """
        if formats is None:
            formats = ["json"]
        
        # standardised export format
        export_data = {
            "question_id": question_id,
            "question_text": question_config.get("label", {}),
            "type": question_config.get("type"),
            "n": analysis_result.get("n", 0),
            "data": analysis_result.get("data", {}),
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "handler": question_config.get("handler"),
            }
        }
        
        # add percentages if present
        if "percentages" in analysis_result:
            export_data["percentages"] = analysis_result["percentages"]
        
        # add cluster information if present
        if "clusters" in analysis_result:
            export_data["clusters"] = analysis_result["clusters"]
        
        saved_files = {}
        
        if "json" in formats:
            saved_files["json"] = self.export_json(
                export_data, question_id, subdir="questions"
            )
        
        if "csv" in formats and "data" in analysis_result:
            data = analysis_result["data"]
            if isinstance(data, (dict, pd.DataFrame)):
                saved_files["csv"] = self.export_csv(
                    data, question_id, subdir="questions"
                )
        
        return saved_files
    
    def export_all_results(
        self,
        results: dict[str, dict],
        filename: str = "survey_results"
    ) -> Path:
        """
        Exports all analysis results into one file.
        
        Args:
            results: dictionary {question_id: analysis_result}
            filename: file name
        
        Returns:
            path of the file
        """
        export_data = {
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "question_count": len(results)
            },
            "questions": results
        }
        
        return self.export_json(export_data, filename)
    
    def export_cluster_mapping(
        self,
        question_id: str,
        clusters: list[dict],
        response_mapping: dict[int, str]
    ) -> Path:
        """
        Exports the cluster assignment of free-text questions.
        
        Args:
            question_id: id of the question
            clusters: list of the clusters
            response_mapping: {answer index: cluster name}
        
        Returns:
            path of the file
        """
        export_data = {
            "question_id": question_id,
            "clusters": [
                {
                    "name": c.get("name"),
                    "description": c.get("description"),
                    "count": len(c.get("answer_ids", [])),
                    "answer_ids": c.get("answer_ids", [])
                }
                for c in clusters
            ],
            "response_mapping": response_mapping
        }
        
        return self.export_json(
            export_data,
            f"{question_id}_clusters",
            subdir="clusters"
        )
    
    def export_segmentation_results(
        self,
        question_id: str,
        segmented_results: dict[str, dict]
    ) -> Path:
        """
        Exports segmented results.
        
        Args:
            question_id: id of the question
            segmented_results: {segment_key: analysis_result}
        
        Returns:
            path of the file
        """
        return self.export_json(
            {
                "question_id": question_id,
                "segments": segmented_results
            },
            f"{question_id}_segmented",
            subdir="segmented"
        )


class ThemeExporter:
    """Exports theme mappings for the dashboard."""
    
    def __init__(self, output_dir: Path):
        self.output_dir = Path(output_dir)
    
    def export_themes(
        self,
        themes: dict[str, dict],
        filename: str = "themes"
    ) -> Path:
        """
        Exports the theme mapping as YAML.
        
        Args:
            themes: themes dictionary
            filename: file name
        
        Returns:
            path of the file
        """
        import yaml
        
        self.output_dir.mkdir(parents=True, exist_ok=True)
        filepath = self.output_dir / f"{filename}.yaml"
        
        with open(filepath, 'w', encoding='utf-8') as f:
            yaml.dump(
                {"themes": themes},
                f,
                default_flow_style=False,
                allow_unicode=True,
                sort_keys=False
            )
        
        logger.info(f"Themes exported: {filepath}")
        return filepath


def create_exporter(output_dir: str | Path) -> DataExporter:
    """
    Factory function of the DataExporter.
    
    Args:
        output_dir: output directory
    
    Returns:
        DataExporter instance
    """
    return DataExporter(Path(output_dir))
