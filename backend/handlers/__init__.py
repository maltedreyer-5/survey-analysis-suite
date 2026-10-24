# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 Malte Dreyer
"""
Handler registry of the survey analysis tool.

Manages all available handler types and returns the matching handler for
each question type.
"""

import logging
from typing import Dict, Type, Optional

from .base import BaseHandler
from .single_choice import SingleChoiceHandler
from .multi_choice import MultiChoiceHandler
from .multi_choice_binary import MultiChoiceBinaryHandler
from .matrix_likert import MatrixLikertHandler
from .freetext import FreetextHandler
from .ranking import RankingHandler
from .cooperation_matrix import CooperationMatrixHandler
from .numeric import NumericHandler
from .numeric_matrix import NumericMatrixHandler
from .date import DateHandler

logger = logging.getLogger(__name__)


# handler registry: handler type -> handler class
HANDLER_REGISTRY: Dict[str, Type[BaseHandler]] = {
    'single_choice': SingleChoiceHandler,
    'multi_choice': MultiChoiceHandler,
    'multi_choice_binary': MultiChoiceBinaryHandler,
    'matrix_likert': MatrixLikertHandler,
    'freetext': FreetextHandler,
    'ranking': RankingHandler,
    'cooperation_matrix': CooperationMatrixHandler,
    'numeric': NumericHandler,
    'numeric_matrix': NumericMatrixHandler,
    'date': DateHandler,
}


def register_handler(handler_type: str, handler_class: Type[BaseHandler]) -> None:
    """
    Registers a new handler type.
    
    Args:
        handler_type: name of the handler type
        handler_class: handler class (must inherit from BaseHandler)
    """
    if not issubclass(handler_class, BaseHandler):
        raise ValueError(f"Handler class must inherit from BaseHandler: {handler_class}")
    
    HANDLER_REGISTRY[handler_type] = handler_class
    logger.info(f"Handler registered: {handler_type} -> {handler_class.__name__}")


def get_handler(
    handler_type: str,
    question_id: str,
    config: dict
) -> Optional[BaseHandler]:
    """
    A handler instance of the given type.
    
    Args:
        handler_type: name of the handler type
        question_id: id of the question
        config: configuration of the question group
    
    Returns:
        handler instance, or None if not found
    """
    handler_class = HANDLER_REGISTRY.get(handler_type)
    
    if handler_class is None:
        logger.warning(f"No handler found for the type: {handler_type}")
        return None
    
    try:
        # make sure the id is in the config
        config_with_id = {**config, 'id': question_id}
        handler = handler_class(config=config_with_id)
        logger.debug(f"Handler created: {handler_type} for {question_id}")
        return handler
    except Exception as e:
        logger.error(f"Error creating the handler {handler_type}: {e}")
        return None


def get_available_handlers() -> list[str]:
    """
    A list of all available handler types.
    
    Returns:
        list of the handler type names
    """
    return list(HANDLER_REGISTRY.keys())


def get_handler_info() -> Dict[str, dict]:
    """
    Information on all registered handlers.
    
    Returns:
        dictionary with the handler type as key and an info dict as value
    """
    info = {}
    for handler_type, handler_class in HANDLER_REGISTRY.items():
        info[handler_type] = {
            'class': handler_class.__name__,
            'supported_charts': getattr(handler_class, 'supported_charts', []),
            'doc': handler_class.__doc__ or ''
        }
    return info


# lazy loading of optional handlers
def _load_optional_handlers():
    """Loads optional handlers if available."""
    try:
        from .ranking import RankingHandler
        HANDLER_REGISTRY['ranking'] = RankingHandler
        logger.debug("RankingHandler loaded")
    except ImportError:
        logger.debug("RankingHandler not available")
    
    try:
        from .freetext import FreetextHandler
        HANDLER_REGISTRY['freetext'] = FreetextHandler
        logger.debug("FreetextHandler loaded")
    except ImportError:
        logger.debug("FreetextHandler not available")
    
    try:
        from .cooperation_matrix import CooperationMatrixHandler
        HANDLER_REGISTRY['cooperation_matrix'] = CooperationMatrixHandler
        logger.debug("CooperationMatrixHandler loaded")
    except ImportError:
        logger.debug("CooperationMatrixHandler not available")


# load the optional handlers at import
_load_optional_handlers()


__all__ = [
    'BaseHandler',
    'SingleChoiceHandler',
    'MultiChoiceHandler',
    'MultiChoiceBinaryHandler',
    'MatrixLikertHandler',
    'NumericHandler',
    'HANDLER_REGISTRY',
    'register_handler',
    'get_handler',
    'get_available_handlers',
    'get_handler_info',
]
