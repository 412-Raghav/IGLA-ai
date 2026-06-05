import logging
import anthropic
from config import ANTHROPIC_API_KEY, MODEL_NAME, MAX_TOKENS
from rag.retriever import retrieve_context


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)

logger = logging.getLogger("igla")

client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

def ask_igla(situation: str) -> str:
    """
    Send a tactical situation to Claude with RAG context.
    Retrieves relevant intel from vector database first.
    Returns Claude's opponent-aware tactical response.
    """
    try:
        logger.info("Searching tactical database...")
        context = retrieve_context(situation)
        logger.info("Tactical database search complete")
    except Exception as e:
        logger.error(f"IGLA Error - Database failed: {e}")
        return "Could not retrieve tactical intel"

    system_prompt = """You are IGLA, an elite Valorant tactical AI assistant.
You analyze in-game situations and provide specific, actionable strategies.
Your responses are concise, structured, and immediately actionable.
You think like a professional IGL with 10 years of experience.
When tactical intel is provided, always prioritize it over general knowledge."""

    augmented_message = f"""TACTICAL INTEL FROM DATABASE:
{context}

SITUATION TO ANALYZE:
{situation}

Use the tactical intel above to give a specific, opponent-aware response."""
    try:
        message = client.messages.create(
        model=MODEL_NAME,
        max_tokens=MAX_TOKENS,
        system=system_prompt,
        messages=[
            {
                "role": "user",
                "content": augmented_message
            }
        ]
        )
        logger.info("Claude responded successfully.")
        return message.content[0].text
    except anthropic.APIError as e:
        logger.error(f"IGLA error - Anthropic server failed: {e}")
        return "Not able to connect Anthropic server"
    


if __name__ == "__main__":
    
    user_input = input("Describe the situation: ").strip()
    if user_input == "":
        print("IGLA Error: Query/Input cannot be empty")
    else:
        print("Sending situation to IGLA..\n")
        response = ask_igla(user_input)
        print("IGLA Response:")
        print("="*50)
        print(response)
        print("="*50)