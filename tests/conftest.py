"""
pytest configuration and fixtures.
"""

import os
import sys
import pytest
from pathlib import Path

# Add transcription_studio to path
sys.path.insert(0, str(Path(__file__).parent.parent / "transcription_studio"))


@pytest.fixture
def sample_transcript():
    """Sample transcript text for testing."""
    return """Hello, this is a test transcript.
It has multiple lines of text.
This is useful for testing AI prompts.
The weather today is quite nice.
We should go for a walk later.
"""
