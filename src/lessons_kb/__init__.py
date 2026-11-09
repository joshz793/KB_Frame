'''Lessons-learned knowledge base for computational work (see docs/GUIDE.md).'''
__version__ = '0.3.1'
from .core import (KB, KBError, build_system, make_lesson, transaction, check_calibration, lesson_id, setup_match,  # noqa: F401
                   successors, route_new, SHARED, PROJECT,
                   CORE_CATEGORIES, SCOPES, SETUP_KEYS, CONFIDENCE, SOURCES, MODEL_STATUS, REVIEW_LEVELS,
                   SCHEMA_VERSION)
from .profile import Profile, Field, load_profiles  # noqa: F401
from . import geometry, supplements, config  # noqa: F401
