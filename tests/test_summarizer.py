import pytest
from unittest.mock import MagicMock, patch
from handler.summarizer import NotesSummarizer


def test_notes_summarizer_init_default():
    """Test that NotesSummarizer initializes with default model and loads API key."""
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-env-key"}):
        summarizer = NotesSummarizer()
        assert summarizer.api_key == "test-env-key"
        assert summarizer.llm.model == "gemini-2.5-flash"


def test_notes_summarizer_init_explicit():
    """Test initializing with custom model name and explicit API key."""
    summarizer = NotesSummarizer(model_name="gemini-1.5-pro", api_key="custom-key")
    assert summarizer.api_key == "custom-key"
    assert summarizer.llm.model == "gemini-1.5-pro"


@pytest.mark.parametrize("empty_input", [None, "", "   ", "\n\t"])
def test_summarize_empty_or_whitespace(empty_input):
    """Test that empty or whitespace-only inputs immediately return a generic message without invoking LLM."""
    summarizer = NotesSummarizer(api_key="test-key")
    summarizer.chain = MagicMock()
    
    result = summarizer.summarize(empty_input)
    assert result == "No notes provided to summarize."
    summarizer.chain.invoke.assert_not_called()


def test_summarize_success():
    """Test successful notes summarization using a mocked langchain chain."""
    summarizer = NotesSummarizer(api_key="test-key")
    summarizer.chain = MagicMock()
    mock_response = "This is a mocked summary of the incident notes."
    summarizer.chain.invoke.return_value = mock_response
    
    result = summarizer.summarize("Incident occurred at 10:00 AM. DB is down.")
    assert result == mock_response
    summarizer.chain.invoke.assert_called_once_with({"notes": "Incident occurred at 10:00 AM. DB is down."})


def test_summarize_failure():
    """Test that summarizer raises a RuntimeError when langchain chain invocation fails."""
    summarizer = NotesSummarizer(api_key="test-key")
    summarizer.chain = MagicMock()
    summarizer.chain.invoke.side_effect = ValueError("LLM API Error")
    
    with pytest.raises(RuntimeError, match="Error invoking Gemini model for summarization"):
        summarizer.summarize("Some technical logs")
