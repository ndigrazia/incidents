import os
from typing import Optional
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser

# Load environment variables
load_dotenv()


class NotesSummarizer:
    """
    A component to summarize incident notes using the LangChain framework and Gemini.
    """
    def __init__(self, model_name: Optional[str] = None, api_key: Optional[str] = None):
        self.model_name = model_name or os.environ.get("GEMINI_MODEL_NAME", "gemini-2.5-flash")
        # We can try to get the api key from arguments, or from environment variables: GOOGLE_API_KEY or GEMINI_API_KEY
        self.api_key = api_key or os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        
        # Initialize the LangChain ChatGoogleGenerativeAI model.
        # It defaults to reading GOOGLE_API_KEY if not explicitly provided.
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
