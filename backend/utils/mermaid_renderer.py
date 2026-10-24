# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Mermaid diagram renderer for Word documents.

Renders Mermaid code into PNG images through a rendering service configured in
MERMAID_RENDER_URL (e.g. mermaid.ink); without one (the default) no external
request is made and the document shows the diagram as a code block.
Supports:
- placeholder labels from diagram_labels.yaml (multilingual)
- local caching of the rendered PNGs (hash-based)
- retries with exponential backoff
- a fallback to a code block on errors

Usage:
    renderer = MermaidRenderer(lang='en')
    png_bytes = renderer.render(mermaid_code_with_placeholders)
"""

import base64
import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# configuration
# Rendering service for diagrams (e.g. https://mermaid.ink/img/ or a self-hosted one).
# Empty (the default): no external request; the document shows the diagram as a
# code block instead.
MERMAID_INK_URL = os.environ.get("MERMAID_RENDER_URL", "").strip()
# paths relative to backend/, not to the working directory; the cache
# follows SURVEY_DATA_DIR like the database
_BACKEND = Path(__file__).resolve().parent.parent
CACHE_DIR = Path(os.environ.get("SURVEY_DATA_DIR") or _BACKEND / "data") / "diagrams"
LABELS_PATH = _BACKEND / "config" / "diagram_labels.yaml"


def _default_labels_path() -> Path:
    """diagram_labels.yaml of the active survey if it has one, else config/diagram_labels.yaml."""
    try:
        from core import get_active_config_dir
        own = Path(get_active_config_dir()) / "diagram_labels.yaml"
        if own.exists():
            return own
    except Exception:
        pass
    return LABELS_PATH

# retry configuration
MAX_RETRIES = 4
RETRY_DELAYS = [2, 5, 10, 20]  # seconds between attempts


class MermaidRenderer:
    """Renders Mermaid diagrams into PNG, with language support."""
    
    def __init__(self, lang: str = 'de', cache_dir: Path = None, labels_path: Path = None):
        self.lang = lang
        self.cache_dir = cache_dir or CACHE_DIR
        self.labels_path = labels_path or _default_labels_path()
        self.labels = self._load_labels()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _load_labels(self) -> dict:
        """Loads the diagram labels from the YAML file."""
        if not self.labels_path.exists():
            logger.warning(f"Diagram labels not found: {self.labels_path}")
            return {}
        
        try:
            with open(self.labels_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f) or {}
        except Exception as e:
            logger.error(f"Error loading the diagram labels: {e}")
            return {}
    
    def _substitute_labels(self, mermaid_code: str) -> str:
        """
        Replaces placeholders in the Mermaid code by language-specific labels.
        
        Placeholder format: {label_key}
        The labels are loaded from diagram_labels.yaml.
        Fallback chain: target language -> English -> German -> placeholder unchanged
        """
        result = mermaid_code
        
        for key, translations in self.labels.items():
            if not isinstance(translations, dict):
                continue
            
            placeholder = '{' + key + '}'
            if placeholder in result:
                # language-specific label with a fallback
                label = translations.get(self.lang) or translations.get('en') or translations.get('de', key)
                result = result.replace(placeholder, label)
        
        return result
    
    def _get_cache_path(self, resolved_code: str) -> Path:
        """Computes the cache path from a hash of the resolved code."""
        code_hash = hashlib.sha256(resolved_code.encode('utf-8')).hexdigest()[:16]
        return self.cache_dir / f"mermaid_{self.lang}_{code_hash}.png"
    
    def _render_via_api(self, mermaid_code: str) -> Optional[bytes]:
        """
        Renders Mermaid code through the rendering service, with retries.
        
        Uses the neutral theme and limits the width, so the diagrams fit on
        A4 pages.
        
        Returns:
            PNG bytes, or None on errors
        """
        if not MERMAID_INK_URL:
            return None   # no rendering service configured: code block fallback
        import httpx
        
        # put the theme configuration in front (neutral = professional, no bright colours)
        themed_code = mermaid_code
        if '%%{init' not in mermaid_code:
            themed_code = "%%{init: {'theme': 'neutral'}}%%\n" + mermaid_code
        
        # Base64-encode the Mermaid code for the URL
        encoded = base64.urlsafe_b64encode(themed_code.encode('utf-8')).decode('ascii')
        
        # control the size by URL parameters (optimised for the A4 width)
        url = f"{MERMAID_INK_URL}{encoded}?bgColor=white&width=900"
        
        for attempt in range(MAX_RETRIES):
            try:
                logger.debug(
                    f"Mermaid rendering attempt {attempt + 1}/{MAX_RETRIES} "
                    f"({len(mermaid_code)} characters)"
                )
                
                with httpx.Client(timeout=30.0) as client:
                    response = client.get(url)
                
                if response.status_code == 200:
                    content_type = response.headers.get('content-type', '')
                    if 'image' in content_type or len(response.content) > 100:
                        logger.info(
                            f"Mermaid rendering successful "
                            f"({len(response.content)} bytes, attempt {attempt + 1})"
                        )
                        return response.content
                    else:
                        logger.warning(
                            f"Mermaid rendering: unexpected content type: {content_type}"
                        )
                else:
                    logger.warning(
                        f"Mermaid rendering HTTP {response.status_code} "
                        f"(attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    
            except httpx.TimeoutException:
                logger.warning(
                    f"Mermaid rendering timeout (attempt {attempt + 1}/{MAX_RETRIES})"
                )
            except httpx.ConnectError:
                logger.warning(
                    f"Mermaid rendering: no connection "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
            except Exception as e:
                logger.warning(
                    f"Mermaid rendering error: {e} "
                    f"(attempt {attempt + 1}/{MAX_RETRIES})"
                )
            
            # wait before the next attempt (except after the last)
            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[min(attempt, len(RETRY_DELAYS) - 1)]
                logger.info(f"Waiting {delay}s before the next attempt...")
                time.sleep(delay)
        
        logger.error(f"Mermaid rendering failed after {MAX_RETRIES} attempts")
        return None
    
    def render(self, mermaid_code: str) -> Optional[bytes]:
        """
        Renders Mermaid code into PNG.
        
        1. replace the placeholders by language-specific labels
        2. check the cache
        3. on a cache miss: render through the rendering service
        4. cache the result
        
        Args:
            mermaid_code: Mermaid code (may contain placeholders)
            
        Returns:
            PNG bytes, or None (a fallback to a code block is recommended)
        """
        # 1. substitute the labels
        resolved = self._substitute_labels(mermaid_code)
        
        # 2. check the cache
        cache_path = self._get_cache_path(resolved)
        if cache_path.exists():
            logger.debug(f"Mermaid-Cache-Hit: {cache_path.name}")
            return cache_path.read_bytes()
        
        # 3. render
        png_bytes = self._render_via_api(resolved)
        
        if png_bytes:
            # 4. cache
            try:
                cache_path.write_bytes(png_bytes)
                logger.info(f"Mermaid-PNG gecacht: {cache_path.name}")
            except Exception as e:
                logger.warning(f"Writing the cache failed: {e}")
        
        return png_bytes
    
    def render_to_file(self, mermaid_code: str, output_path: Path) -> bool:
        """
        Renders Mermaid code and saves it as a PNG file.
        
        Returns:
            True on success, False on errors
        """
        png_bytes = self.render(mermaid_code)
        if png_bytes:
            output_path.write_bytes(png_bytes)
            return True
        return False
    
    def clear_cache(self, lang: str = None):
        """Deletes cached diagrams (optionally only for one language)."""
        pattern = f"mermaid_{lang}_*.png" if lang else "mermaid_*.png"
        count = 0
        for f in self.cache_dir.glob(pattern):
            f.unlink()
            count += 1
        logger.info(f"Mermaid cache cleared: {count} files")
        return count
