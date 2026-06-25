import pytest
from unittest.mock import MagicMock, patch
from handler.summarizer import NotesSummarizer


def test_notes_summarizer_init_default():
    """Test that NotesSummarizer initializes with default model and loads API key."""
    with patch.dict("os.environ", {"GOOGLE_API_KEY": "test-env-key", "LLM_PROVIDER": "gemini"}):
        summarizer = NotesSummarizer()
        assert summarizer.api_key == "test-env-key"
        assert summarizer.llm.model == "gemini-2.5-flash"


def test_notes_summarizer_init_explicit():
    """Test initializing with custom model name and explicit API key."""
    with patch.dict("os.environ", {"LLM_PROVIDER": "gemini"}):
        summarizer = NotesSummarizer(model_name="gemini-1.5-pro", api_key="custom-key")
        assert summarizer.api_key == "custom-key"
        assert summarizer.llm.model == "gemini-1.5-pro"


def test_notes_summarizer_init_litellm():
    """Test initializing with litellm provider."""
    mock_env = {
        "LITELLM_API_BASE": "https://test.llm/gateway",
        "LITELLM_API_KEY": "test-key",
        "LITELLM_MODEL_NAME": "openai/gemini/llm",
        "LITELLM_CLIENT_ID": "client-id",
        "LITELLM_CLIENT_SECRET": "client-secret",
    }
    with patch.dict("os.environ", mock_env):
        summarizer = NotesSummarizer(provider="litellm")
        assert summarizer.provider == "litellm"
        assert summarizer.llm.model == "openai/gemini/llm"


def test_notes_summarizer_init_litellm_env():
    """Test initializing with litellm provider from environment variable."""
    mock_env = {
        "LLM_PROVIDER": "litellm",
        "LITELLM_API_BASE": "https://test.llm/gateway",
        "LITELLM_API_KEY": "test-key",
        "LITELLM_MODEL_NAME": "openai/gemini/llm",
        "LITELLM_CLIENT_ID": "client-id",
        "LITELLM_CLIENT_SECRET": "client-secret",
    }
    with patch.dict("os.environ", mock_env):
        summarizer = NotesSummarizer()
        assert summarizer.provider == "litellm"
        assert summarizer.llm.model == "openai/gemini/llm"


def test_notes_summarizer_init_litellm_missing_env():
    """Test that ValueError is raised when any required environment variables for LiteLLM are missing."""
    mock_env = {
        "LITELLM_API_BASE": "https://test.llm/gateway",
        # "LITELLM_API_KEY": "test-key",
        "LITELLM_MODEL_NAME": "openai/gemini/llm",
        "LITELLM_CLIENT_ID": "client-id",
        "LITELLM_CLIENT_SECRET": "client-secret",
    }
    with patch.dict("os.environ", mock_env, clear=True):
        with pytest.raises(ValueError, match="Required environment variables are not set: LITELLM_API_KEY"):
            NotesSummarizer(provider="litellm")


def test_notes_summarizer_init_gemini_missing_env():
    """Test that ValueError is raised when any required values for Gemini are missing."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(ValueError, match="Required Gemini values are not set: api_key"):
            NotesSummarizer(provider="gemini")


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
