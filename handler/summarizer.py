import os
from typing import Optional
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

from langchain_litellm import ChatLiteLLM

# Load environment variables
load_dotenv()


class NotesSummarizer:
    """
    A component to summarize incident notes using the LangChain framework and Gemini.
    """
    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None, provider: Optional[str] = None):
        self.provider = provider or os.environ.get("LLM_PROVIDER", "gemini")
        
        if self.provider == "litellm":
            required_vars = [
                "LITELLM_API_BASE",
                "LITELLM_API_KEY",
                "LITELLM_MODEL_NAME",
                "LITELLM_CLIENT_ID",
                "LITELLM_CLIENT_SECRET"
            ]
            missing_vars = [var for var in required_vars if not os.environ.get(var)]
            if missing_vars:
                raise ValueError(f"Required environment variables are not set: {', '.join(missing_vars)}")
            
            self.api_base = os.environ["LITELLM_API_BASE"]
            self.api_key = os.environ["LITELLM_API_KEY"]
            self.model_name = os.environ["LITELLM_MODEL_NAME"]
            self.client_id = os.environ["LITELLM_CLIENT_ID"]
            self.client_secret = os.environ["LITELLM_CLIENT_SECRET"]
            
            self.llm = ChatLiteLLM(
                api_base=self.api_base,  # Your LiteLLM Proxy endpoint
                api_key=self.api_key,
                model=self.model_name,  # Prepend 'litellm_proxy/' to route to your gateway
                extra_headers={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret
                }
            )
        else:
            # Initialize the LangChain ChatGoogleGenerativeAI model.
            # It defaults to reading GOOGLE_API_KEY if not explicitly provided.
            self.model_name = model_name or os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
            self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        
            missing_gemini_vars = []
            if not self.model_name:
                missing_gemini_vars.append("model_name")
            if not self.api_key:
                missing_gemini_vars.append("api_key")
            if missing_gemini_vars:
                raise ValueError(f"Required Gemini values are not set: {', '.join(missing_gemini_vars)}")

            self.llm = ChatGoogleGenerativeAI(
                model=self.model_name,
                google_api_key=self.api_key,
                temperature=0,
            )
        
        # Load prompt templates from "propmts" folder
        current_dir = os.path.dirname(os.path.abspath(__file__))
        propmts_dir = os.path.join(current_dir, "propmts")
        
        with open(os.path.join(propmts_dir, "system_prompt.txt"), "r", encoding="utf-8") as f:
            system_prompt = f.read().strip()
            
        with open(os.path.join(propmts_dir, "human_prompt.txt"), "r", encoding="utf-8") as f:
            human_prompt = f.read().strip()

        # Define prompt template
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", system_prompt),
            ("human", human_prompt)
        ])
        
        # Create langchain chain using expression language (LCEL)
        self.chain = self.prompt | self.llm | StrOutputParser()

    def summarize(self, notes: str) -> str:
        """
        Summarize the given incident notes.
        
        Args:
            notes (str): The notes to be summarized.
            
        Returns:
            str: The summarized text.
        """
        if not notes or not notes.strip():
            return "No notes provided to summarize."
            
        try:
            return self.chain.invoke({"notes": notes})
        except Exception as e:
            raise RuntimeError(f"Error invoking Gemini model for summarization: {str(e)}") from e
