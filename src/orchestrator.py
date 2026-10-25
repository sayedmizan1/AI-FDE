import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from typing import Annotated, TypedDict

# Suppress symlink & TF noise
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "true"
os.environ["TF_ENABLE_ONEDNN_OPTS"] = "0"
os.environ["USE_TF"] = "0"

from langchain_core.messages import BaseMessage, SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# ==========================================
# 1. SETUP & PATH RESOLUTION
# ==========================================
script_dir = Path(__file__).resolve().parent
project_root = script_dir.parent

if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from src.agent_tool import (
    query_telemetry_db,
    fetch_corridor_conditions,
    search_compliance_sop,
)

load_dotenv(project_root / ".env")


class AgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ==========================================
# 2. FACTORY INITIALIZATION: AGENT REASONER LLM
# ==========================================
AGENT_LLM_SETTING = os.getenv("Agent_llm", "OLLAMA").strip().upper()

if AGENT_LLM_SETTING == "OPENAI":
    print("[Brain Mode] Utilizing Cloud OpenAI Reasoner (gpt-4o)...")
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=os.getenv("OPENAI_MODEL", "gpt-4o"),
        temperature=0
    )

elif AGENT_LLM_SETTING == "GROQ":
    print("[Brain Mode] Utilizing Groq Cloud Reasoner (openai/gpt-oss-120b)...")
    from langchain_groq import ChatGroq

    llm = ChatGroq(
        model_name=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        temperature=0,
        groq_api_key=os.getenv("GROQ_API_KEY")
    )

elif AGENT_LLM_SETTING == "DEEPSEEK":
    print("[Brain Mode] Utilizing DeepSeek Cloud Reasoner...")
    from langchain_openai import ChatOpenAI

    llm = ChatOpenAI(
        model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        temperature=0,
        openai_api_key=os.getenv("DEEPSEEK_API_KEY"),
        base_url="https://api.deepseek.com",
        max_tokens=2048,
    )

elif AGENT_LLM_SETTING == "GEMINI" or AGENT_LLM_SETTING == "GOOGLE":
    print("[Brain Mode] Utilizing Google Gemini Reasoner...")
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
        llm = ChatGoogleGenerativeAI(
            model=os.getenv("GEMINI_MODEL", "gemini-2.0-flash"),
            temperature=0,
            google_api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        )
    except ImportError:
        print("[Warning] langchain_google_genai not installed. Falling back to OpenAI compatible interface.")
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model="gpt-4o", temperature=0)

else:  # FALLBACK / DEFAULT RUNNER MODE: OLLAMA
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    print(f"[Brain Mode] Local Fallback Activated: Binding Ollama ({ollama_model})...")
    try:
        from langchain_ollama import ChatOllama
        llm = ChatOllama(model=ollama_model, temperature=0)
    except ImportError:
        # Ollama exposes a standard OpenAI-compatible API on port 11434
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=ollama_model,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1"),
            api_key="ollama",
            temperature=0,
        )

# Bind Tools to Reasoner LLM
fde_tools = [query_telemetry_db, fetch_corridor_conditions, search_compliance_sop]
llm_with_tools = llm.bind_tools(fde_tools)


# Load system prompt
prompt_path = project_root / "src" / "prompts" / "system_prompt.txt"
try:
    with open(prompt_path, "r", encoding="utf-8") as f:
        system_instructions = f.read()
except FileNotFoundError:
    system_instructions = "You are a helpful Logistics AI Assistant for cold-chain fleet operations."

system_prompt = SystemMessage(content=system_instructions)


# ==========================================
# 3. GRAPH ARCHITECTURE ASSEMBLY
# ==========================================
def reasoning_node(state: AgentState):
    # Prepend system prompt to conversation if not already first message
    messages = state["messages"]
    if not messages or not isinstance(messages[0], SystemMessage):
        messages = [system_prompt] + list(messages)
    
    response = llm_with_tools.invoke(messages)
    return {"messages": [response]}


print("[System] Compiling LangGraph FDE Orchestrator...")
graph_builder = StateGraph(AgentState)
graph_builder.add_node("reasoner", reasoning_node)
graph_builder.add_node("tools", ToolNode(fde_tools))

graph_builder.add_edge(START, "reasoner")
graph_builder.add_conditional_edges("reasoner", tools_condition)
graph_builder.add_edge("tools", "reasoner")

fde_agent = graph_builder.compile(checkpointer=MemorySaver())


# ==========================================
# 4. CHAT LOOP TESTING PANEL
# ==========================================
if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("FDE Supply Chain Orchestrator State Machine Online")
    print(
        f"Configured: [LLM: {AGENT_LLM_SETTING}] -> [Embeddings: {os.getenv('Embeddings_model', 'LOCAL')}]"
    )
    print("=" * 60 + "\n")

    thread_config = {"configurable": {"thread_id": "production_test_1"}}
    print("Dispatcher console ready. Type 'quit' or 'exit' to stop.")

    while True:
        try:
            user_input = input("\nDispatcher > ")
            if not user_input.strip() or user_input.lower() in ["exit", "quit"]:
                print("Exiting session.")
                break

            events = fde_agent.stream(
                {"messages": [("user", user_input)]},
                config=thread_config,
                stream_mode="updates",
            )
            for event in events:
                for node_name, node_state in event.items():
                    if node_name == "tools":
                        print("   [System] Retrieving external data elements via ToolNode...")
                    elif node_name == "reasoner":
                        latest_msg = node_state["messages"][-1]
                        if latest_msg.content:
                            clean_text = latest_msg.content.encode("ascii", errors="replace").decode("ascii")
                            print(f"\nFDE Agent:\n{clean_text}")
        except KeyboardInterrupt:
            print("\nSession ended by user.")
            break
        except Exception as e:
            err_str = str(e)
            print(f"\n[Error during execution]: {err_str}")
            if "Connection error" in err_str or "refused" in err_str:
                print("\n[HINT] Connection to LLM failed:")
                if AGENT_LLM_SETTING == "OLLAMA":
                    print("  -> Ollama is not running. Start Ollama with 'ollama serve' or 'ollama run qwen2.5:7b'")
                    print("  -> OR switch to a cloud LLM in .env: set Agent_llm=GROQ (free), OPENAI, or DEEPSEEK")
                elif AGENT_LLM_SETTING == "GROQ":
                    print("  -> Check that GROQ_API_KEY is set in your .env file.")
                elif AGENT_LLM_SETTING == "OPENAI":
                    print("  -> Check that OPENAI_API_KEY is set in your .env file.")
                elif AGENT_LLM_SETTING == "DEEPSEEK":
                    print("  -> Check that DEEPSEEK_API_KEY is set in your .env file.")
